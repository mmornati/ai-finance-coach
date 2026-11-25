"""The household's daily brief for Horae (contract v1 of horae-dev/plugin-mcp-brief, ``schemas/brief/v1.json``).

Horae's ``mcp-brief.collect`` calls the ``brief`` tool once a day and turns the answer into a section of the morning note, tasks and, for
``crit`` alerts only, a push. The document leaves this machine and ends up in a note that may be synced and backed up, so:

* **Aggregates only.** Counts, a month-to-date total, budget percentages, days left on a consent. Never a transaction, a merchant, a
  description, a bank or account name, an IBAN, a person, an amount of one payment. Every text is built here from a fixed template and
  numbers; the only words that come from the data are budget targets, which are taxonomy categories, and those pass the privacy guard too.
* **A child's money stays out**, as in the alerts (``kid_budget`` is a local-only kind): accounts a child owns or with purpose ``kids``,
  payments attributed to a child, and budgets of a child are left out of the spending total and the budget alerts. (The review-queue
  count is the owner's chore and counts every account the review queue shows.)
* **Stable keys and ids.** Horae dedupes on them. A consent, an account group or a budget is named by a short hash (never its id), a chore
  task by its ISO week (one task a week at most, however many runs see it).
* **Fail closed.** Every text passes :class:`coach.mcp.guard.PrivacyGuard` (household names, account labels, IBAN, e-mail, secrets,
  paths): an item that trips it is dropped. The result is validated against the vendored v1 schema and the plugin's extra rules; a
  document that is not valid, or larger than :data:`MAX_BYTES`, is replaced by a fixed ``error`` brief. Nothing here writes anything.

``crit`` is kept rare: only an expired or revoked bank consent, or a bank whose syncs keep failing (twice the alert threshold), i.e.
things that stop the data today. A budget over its envelope is ``warn``; a budget on course to overrun is ``info``.
"""
from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger("coach.horae")

SCHEMA_VERSION = 1
SERVICE_ID = "finance-coach"
SERVICE_NAME = "Finance"
VALIDITY = dt.timedelta(hours=24)          # the plugin accepts up to 48 h; a daily brief is stale after a day
MAX_BYTES = 16 * 1024                      # the plugin's cap is 64 KiB for the whole HTTP answer (the document is sent twice in it)
STALE_DAYS = 7                             # newest transaction older than this: the brief is `degraded`
MAX_ALERTS = MAX_TASKS = 10
MAX_LINKS = 5
SCHEMA_FILE = Path(__file__).with_name("brief-v1.schema.json")
# vendored byte for byte from horae-dev/plugin-mcp-brief (tests/test_horae_brief.py pins the hash)
SCHEMA_SOURCE = ("https://github.com/horae-dev/plugin-mcp-brief/blob/81d2dc2011e1b694f5d38706574c2258627fb69a/schemas/brief/v1.json")
SEVERITY_ORDER = {"crit": 0, "warn": 1, "info": 2}
_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?$")


# ---------------------------------------------------------------- what is computed (aggregates only)

@dataclass
class BudgetFact:
    ref: str                     # short hash of the budget id
    label: str                   # the taxonomy category or group, in words
    status: str                  # over | at_risk | ok
    used_pct: Optional[int]
    projected_pct: Optional[int]


@dataclass
class ConsentFact:
    ref: str                     # short hash of the bank session id
    status: str                  # expired | revoked | urgent | expiring | unknown
    days_left: Optional[int]
    until: Optional[str]         # YYYY-MM-DD


@dataclass
class SyncFact:
    ref: str                     # short hash of the bank session id
    accounts: int
    failures: int
    broken: bool                 # failing for twice the alert threshold


@dataclass
class Facts:
    today: dt.date
    month: str
    spent_c: int
    has_data: bool
    last_data: Optional[dt.date]
    budgets: list = field(default_factory=list)
    consents: list = field(default_factory=list)
    syncs: list = field(default_factory=list)
    uncategorised_tx: int = 0
    unsure_merchants: int = 0
    pending_proposals: int = 0
    open_questions: int = 0


def ref(kind: str, value: str) -> str:
    """A stable, opaque name for an identifier that must not leave the machine (a session id, a budget id)."""
    return hashlib.sha256(f"horae-brief:{kind}:{value}".encode()).hexdigest()[:10]


def category_words(target: str) -> str:
    """'food.groceries' -> 'Groceries', 'group:food' -> 'Food (all)'."""
    if target.startswith("group:"):
        return target.removeprefix("group:").replace("_", " ").strip().capitalize() + " (all)"
    return target.rsplit(".", 1)[-1].replace("_", " ").strip().capitalize()


def _children(store) -> set[str]:
    try:
        return {m.id for m in store.members() if getattr(m, "role", None) == "child"}
    except Exception:                                                      # noqa: BLE001 - no household file: no child
        return set()


def _sync_streaks(con, dead_sessions: set[str], n_min: int) -> list[SyncFact]:
    """Per bank session: the accounts whose last `n_min`+ syncs failed (the alert engine's rule), without any label."""
    groups: dict[str, list[int]] = {}
    for uid, sid in con.execute("SELECT uid, COALESCE(session_id, '') FROM accounts WHERE source='api' AND COALESCE(exclude, 0)=0").fetchall():
        if sid in dead_sessions:
            continue                                     # the expired consent already says why
        streak = 0
        for (ok,) in con.execute("SELECT ok FROM sync_log WHERE account_uid=? ORDER BY ran_at DESC, rowid DESC", (uid,)).fetchall():
            if ok:
                break
            streak += 1
        if streak >= n_min:
            groups.setdefault(sid or uid, []).append(streak)
    return [SyncFact(ref("session", sid), len(v), max(v), max(v) >= 2 * n_min) for sid, v in sorted(groups.items())]


def collect(con, cfg, ds, store, *, review_rows: Iterable[dict], now: dt.datetime, recurring=None) -> Facts:
    """The facts behind the brief, from the existing layers: the analytics dataset (month-to-date spending, budget status), the consent
    lifecycle, the sync log, the review queue and the memory (pending proposals, open questions)."""
    from coach.analytics.budgets import budget_status
    from coach.analytics.common import month_key
    from coach.analytics.dataset import is_spending
    from coach.ingest import consent as consent_mod
    from coach.memory import proposals as prop_mod

    today = ds.today
    month = month_key(today)
    kids = _children(store)
    kid_accounts = {uid for uid, a in ds.accounts.items() if a.owner in kids or (a.purpose or "") == "kids"}
    adult_txs = [t for t in ds.txs if t.account not in kid_accounts and t.person not in kids]
    spent = sum(-t.amount_c for t in adult_txs
                if t.month == month and is_spending(t.category) and not t.is_saved and not t.is_one_off)
    budgets = []
    if ds.memory.budgets:
        for p in budget_status(ds, recurring=recurring).budgets:
            if p.owner in kids or p.account in kid_accounts:
                continue
            budgets.append(BudgetFact(ref("budget", p.id), category_words(p.target), p.status,
                                      None if p.percent_used is None else int(round(p.percent_used * 100)),
                                      None if p.projected_percent is None else int(round(p.projected_percent * 100))))
    consents = []
    dead = set()
    for c in consent_mod.list_consents(con, now):
        if c.status == "ok":
            continue
        if c.status in consent_mod.DEAD:
            dead.add(c.session_id)
        consents.append(ConsentFact(ref("session", c.session_id), c.status, c.days_left, (c.valid_until or "")[:10] or None))
    try:
        n_min = max(1, int(cfg.alert_settings.sync_failures))
    except Exception:                                                      # noqa: BLE001 - an invalid [alerts] table: the default
        n_min = 3
    unc = unsure = 0
    for r in review_rows:
        if r.get("reason") == "low_confidence":
            unsure += 1
        else:
            unc += int(r.get("n") or 0)
    try:
        pending = len(prop_mod.listing(store, "pending"))
    except Exception:                                                      # noqa: BLE001
        pending = 0
    try:
        questions = sum(1 for q in store.questions() if q.status == "open")
    except Exception:                                                      # noqa: BLE001
        questions = 0
    last = max((t.date for t in adult_txs), default=None)
    return Facts(today, month, spent, bool(ds.accounts), last, budgets, consents, _sync_streaks(con, dead, n_min), unc, unsure,
                 pending, questions)


# ---------------------------------------------------------------- the document

def _stamp(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _money(cents: int) -> str:
    return f"EUR {int(round(cents / 100)):,}"


def link_base(cfg) -> Optional[str]:
    """``https://<first [ui] allowed_hosts>/`` when the web app is served remotely (the host the one-time login link uses), else None:
    the brief then carries no link."""
    if not getattr(cfg, "ui_allow_remote", False) or not getattr(cfg, "ui_allowed_hosts", ()):
        return None
    host = str(cfg.ui_allowed_hosts[0]).strip().lower()
    return f"https://{host}/" if _HOST_RE.match(host) else None


def compose(f: Facts, now: dt.datetime, base: Optional[str]) -> dict:
    def link(path: str) -> dict:
        return {"link": base + path} if base else {}

    month_name = calendar.month_name[f.today.month]
    week = "{}-W{:02d}".format(*f.today.isocalendar()[:2])
    if not f.has_data:
        headline = "No bank data yet: connect a bank in the app."
    else:
        headline = f"{month_name} so far: {_money(f.spent_c)} spent"
        if f.budgets:
            over = sum(b.status == "over" for b in f.budgets)
            risk = sum(b.status == "at_risk" for b in f.budgets)
            ok = sum(b.status == "ok" for b in f.budgets)
            if over or risk:
                parts = ([f"{over} over"] if over else []) + ([f"{risk} at risk"] if risk else []) + ([f"{ok} on track"] if ok else [])
                headline += "; budgets: " + ", ".join(parts) + "."
            else:
                headline += "; " + ("the budget is" if ok == 1 else f"all {ok} budgets are") + " on track."
        else:
            headline += " (no budgets set)."

    alerts: list[dict] = []
    tasks: list[dict] = []
    for c in f.consents:
        if c.status in ("expired", "revoked"):
            alerts.append({"key": f"consent.{c.ref}.expired", "severity": "crit",
                           "title": "A bank connection has expired and no longer syncs",
                           "body": "Reconnect the bank in the app (Connections). Until then its figures go stale.", **link("connections")})
            tasks.append({"id": f"consent.{c.ref}.reconnect", "title": "Reconnect an expired bank connection",
                          "due": f.today.isoformat(), **link("connections")})
            continue
        if c.status == "unknown":
            alerts.append({"key": f"consent.{c.ref}.unverified", "severity": "warn", "title": "A bank consent could not be verified",
                           "body": "Refresh the consents in the app (Connections).", **link("connections")})
        elif c.status == "urgent":
            left = "today" if (c.days_left or 0) <= 0 else "in " + _n(c.days_left, "day", "days")
            alerts.append({"key": f"consent.{c.ref}.expiring", "severity": "warn", "title": f"A bank consent expires {left}",
                           "body": "Renew it in the app (Connections) to keep the bank syncing.", **link("connections")})
        if c.until and c.status in ("urgent", "expiring", "unknown"):
            tasks.append({"id": f"consent.{c.ref}.renew.{c.until}", "title": f"Renew a bank consent before {c.until}",
                          "due": c.until, **link("connections")})
    for s in f.syncs:
        what = "A bank connection is broken" if s.broken else "A bank is not syncing"
        alerts.append({"key": f"sync.{s.ref}.failing", "severity": "crit" if s.broken else "warn",
                       "title": f"{what}: {_n(s.failures, 'sync', 'syncs')} failed in a row",
                       "body": f"{_n(s.accounts, 'account is', 'accounts are')} not up to date. Check Connections in the app.",
                       **link("connections")})
    for b in f.budgets:
        if b.status == "over" and b.used_pct is not None:
            alerts.append({"key": f"budget.{b.ref}.{f.month}.over", "severity": "warn",
                           "title": f"{b.label} budget exceeded: {b.used_pct}% used", **link("budgets")})
        elif b.status == "at_risk" and b.projected_pct is not None:
            alerts.append({"key": f"budget.{b.ref}.{f.month}.at-risk", "severity": "info",
                           "title": f"{b.label} budget on course to be exceeded: {b.projected_pct}% projected", **link("budgets")})
    if f.uncategorised_tx:
        tasks.append({"id": f"review.uncategorised.{week}",
                      "title": f"Categorise {_n(f.uncategorised_tx, 'uncategorised transaction', 'uncategorised transactions')}",
                      **link("review")})
    if f.unsure_merchants:
        tasks.append({"id": f"review.low-confidence.{week}",
                      "title": f"Check {_n(f.unsure_merchants, 'merchant label', 'merchant labels')} the AI was unsure about",
                      **link("review")})
    if f.pending_proposals:
        tasks.append({"id": f"memory.proposals.{week}",
                      "title": f"Review {_n(f.pending_proposals, 'pending memory proposal', 'pending memory proposals')}",
                      **link("memory")})
    if f.open_questions:
        tasks.append({"id": f"memory.questions.{week}",
                      "title": f"Answer {_n(f.open_questions, 'open question', 'open questions')} from the coach", **link("memory")})
    alerts.sort(key=lambda a: SEVERITY_ORDER[a["severity"]])          # stable: crit first, document order otherwise
    tasks.sort(key=lambda t: (t.get("due") is None, t.get("due") or ""))   # dated tasks first, soonest first

    status: dict[str, Any] = {"state": "ok", "reason": None}
    if not f.has_data or f.last_data is None:
        status = {"state": "degraded", "reason": "No bank data yet."}
    elif any(c.status in ("expired", "revoked") for c in f.consents):
        status = {"state": "degraded", "reason": "A bank connection has expired: the figures may be incomplete."}
    elif any(s.broken for s in f.syncs):
        status = {"state": "degraded", "reason": "A bank has not synced for several runs: the figures may be incomplete."}
    elif (f.today - f.last_data).days > STALE_DAYS:
        status = {"state": "degraded", "reason": f"The newest transaction is {(f.today - f.last_data).days} days old: "
                                                 "the figures may be incomplete."}

    links = []
    if base:
        links.append({"label": "Finance coach", "url": base})
        if f.budgets:
            links.append({"label": "Budgets", "url": base + "budgets"})
        if f.uncategorised_tx or f.unsure_merchants:
            links.append({"label": "Review queue", "url": base + "review"})
        if f.consents or f.syncs:
            links.append({"label": "Connections", "url": base + "connections"})
        if f.pending_proposals or f.open_questions:
            links.append({"label": "Memory", "url": base + "memory"})
    now = now.replace(microsecond=0)
    doc = {"schema_version": SCHEMA_VERSION, "service": {"id": SERVICE_ID, "name": SERVICE_NAME},
           "generated_at": _stamp(now), "valid_until": _stamp(now + VALIDITY), "status": status, "headline": headline,
           "alerts": alerts[:MAX_ALERTS], "tasks": tasks[:MAX_TASKS], "links": links[:MAX_LINKS]}
    return doc


def error_brief(now: dt.datetime) -> dict:
    """The fixed answer when no trustworthy brief can be made (the data could not be read, a check failed). No detail: the cause is in
    the app's own log as a type name."""
    now = now.replace(microsecond=0)
    return {"schema_version": SCHEMA_VERSION, "service": {"id": SERVICE_ID, "name": SERVICE_NAME},
            "generated_at": _stamp(now), "valid_until": _stamp(now + VALIDITY),
            "status": {"state": "error", "reason": "The finance data could not be read just now."},
            "headline": "Finance brief unavailable.", "alerts": [], "tasks": [], "links": []}


# ---------------------------------------------------------------- checks

@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def validate(doc: dict) -> list[str]:
    """Why `doc` is not a valid v1 brief for the plugin (empty when it is): the schema, then the rules the plugin adds (unique keys and
    ids, real dates, a validity window after `generated_at` and at most 48 hours long)."""
    import jsonschema
    errs = [e.message[:120] for e in jsonschema.Draft202012Validator(schema()).iter_errors(doc)]
    if errs:
        return errs
    keys = [a["key"] for a in doc.get("alerts", [])]
    ids = [t["id"] for t in doc.get("tasks", [])]
    if len(set(keys)) != len(keys):
        errs.append("repeated alert key")
    if len(set(ids)) != len(ids):
        errs.append("repeated task id")
    try:
        gen = dt.datetime.fromisoformat(doc["generated_at"].replace("Z", "+00:00"))
        until = dt.datetime.fromisoformat(doc["valid_until"].replace("Z", "+00:00"))
        if not gen < until <= gen + dt.timedelta(hours=48):
            errs.append("validity window")
        for t in doc.get("tasks", []):
            if t.get("due") is not None:
                dt.date.fromisoformat(t["due"])
    except ValueError:
        errs.append("impossible date")
    return errs


def _texts(item: dict) -> dict:
    """The words of an item (what the guard reads). Links are left out: they are built from [ui] allowed_hosts, not from the data, and a
    host name may legitimately contain a word the guard knows (the household's own domain)."""
    return {k: v for k, v in item.items() if k not in ("link", "url", "key", "id")}


def guarded(doc: dict, violations: Callable[[Any], list]) -> tuple[dict, int]:
    """`doc` without the items whose text trips the privacy guard (and the count of what was dropped). A headline or a reason that trips
    it is replaced by a fixed sentence."""
    dropped = 0
    for name in ("alerts", "tasks", "links"):
        kept = []
        for item in doc.get(name, []):
            if violations(_texts(item)):
                dropped += 1
            else:
                kept.append(item)
        doc[name] = kept
    if violations(doc["headline"]):
        doc["headline"] = "Your finance brief is ready in the app."
        dropped += 1
    if doc["status"].get("reason") and violations(doc["status"]["reason"]):
        doc["status"]["reason"] = "Some figures may be incomplete."
        dropped += 1
    return doc, dropped


def finalize(doc: dict, now: dt.datetime, violations: Callable[[Any], list]) -> dict:
    doc, dropped = guarded(doc, violations)
    problems = validate(doc)
    size = len(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode())
    if problems or size > MAX_BYTES:
        log.warning("horae brief: replaced by the error brief (%d schema problem(s), %d bytes)", len(problems), size)
        return error_brief(now)
    log.info("horae brief: state=%s alerts=%d tasks=%d links=%d dropped=%d", doc["status"]["state"], len(doc["alerts"]),
             len(doc["tasks"]), len(doc["links"]), dropped)
    return doc


def produce(load: Callable[[], tuple], *, now: Optional[dt.datetime] = None, base: Optional[str] = None) -> dict:
    """The brief. `load()` returns ``(facts, violations)`` (the facts and the privacy guard's check); any failure there gives the fixed
    error brief, never an exception or a detail."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        facts, violations = load()
        doc = compose(facts, now, base)
    except Exception as e:                                                 # noqa: BLE001 - the caller gets a valid document
        log.warning("horae brief: the facts could not be computed (%s)", type(e).__name__)
        return error_brief(now)
    return finalize(doc, now, violations)


def violations_for(store, con, cfg) -> Callable[[Any], list]:
    from coach.mcp.guard import PrivacyGuard
    return PrivacyGuard(store, con, cfg).violations
