"""The signals the alert engine turns into events (E10-2). Nothing is computed here that the rest of the app does not already
compute: the cards of the insights feed (``api.views.build_insights``), the consent lifecycle (``ingest.consent``) and the sync log.

Each :class:`Candidate` has a DEDUPE KEY that identifies the underlying situation, not its wording or its date, so a signal that
moves a little between two checks (a projected date, a budget status) is still the same event:

    consent         session id + threshold (d14, d3, expired): three distinct, successive events
    sync_failing    account + the first failure of the current streak (a new streak is a new event)
    unusual_charge  the anomaly id (a hash of the transactions it is about)
    price_increase  the price-change id
    low_balance     account + negative / at risk (the date of the projection is a payload field, not part of the key)
    budget          budget target + month (over vs at-risk is an escalation of the same event)
    loan_alert      the loan alert id;  loa_end: the lease end reminder id
    unused_subscription  the contract / series;  contract_notice: the notice date;  decision_contradicted: the decision
    llm_usage_high       the month (E12-4; local feed only)
    kid_budget           the kid budget + its period start, or the unusual payment (E14-6; local feed only: no channel, not even a count)
    scheme_end / scheme_check / rent_missing   the rental card id: the property + the commitment end and the reminder step / the declared limit
                         and the figures / the month without rent (E15; local content, external messages stay minimal)
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Optional

from coach.alerts.settings import RANK, AlertSettings
from coach.analytics.common import to_cents
from coach.i18n_msg import server_msg


@dataclass
class Candidate:
    kind: str
    key: str
    severity: str
    title: str
    body: str
    payload: dict = field(default_factory=dict)
    title_msg: Optional[dict] = None       # the title / body for the web ({code, params, text}); stored in the event's payload
    body_msg: Optional[dict] = None        # (``title_msg`` / ``body_msg`` keys), local only: never part of a channel message

    @property
    def id(self) -> str:
        return event_id(self.key)


def event_id(key: str) -> str:
    return "alr_" + hashlib.sha1(key.encode()).hexdigest()[:12]


def _cents(v) -> Optional[int]:
    try:
        return abs(to_cents(v)) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------- consent

def consent_candidates(con, now: Optional[dt.datetime] = None) -> list[Candidate]:
    from coach.ingest import consent as C
    out = []
    for c in C.list_consents(con, now):
        crossed = C.alert_thresholds(c)
        if not crossed:
            continue
        top = crossed[-1]
        sev = "high" if top in ("d3", "expired") else "medium"
        title = C.describe(c).split(" - run ")[0]
        body = "Reconnect the bank from the app (Connections) or with `coach reconnect`. Until then the data goes stale."
        out.append(Candidate("consent", f"consent:{c.session_id}:{top}", sev, title, body,
                             {"session_id": c.session_id, "bank": c.bank, "threshold": top, "days_left": c.days_left,
                              "valid_until": c.valid_until},
                             title_msg=C.describe_msg(c, title), body_msg=server_msg("alert.consent.reconnect", body)))
    return out


# ---------------------------------------------------------------- sync failing

def sync_candidates(con, n_min: int) -> list[Candidate]:
    out = []
    for uid, label, excluded in con.execute("SELECT uid, COALESCE(label, name, uid), COALESCE(exclude, 0) FROM accounts "
                                            "WHERE source='api'").fetchall():
        if excluded:
            continue
        rows = con.execute("SELECT ran_at, ok FROM sync_log WHERE account_uid=? ORDER BY ran_at DESC, rowid DESC", (uid,)).fetchall()
        streak = []
        for ran_at, ok in rows:
            if ok:
                break
            streak.append(ran_at)
        if len(streak) < n_min:
            continue
        first = streak[-1]
        sev = "high" if len(streak) >= 2 * n_min else "medium"
        title = f"{label}: the last {len(streak)} syncs failed"
        body = ("The transactions of this account are not up to date. Run `coach health` to see the error; an expired "
                "consent needs `coach reconnect`.")
        out.append(Candidate("sync_failing", f"sync:{uid}:{first}", sev, title, body,
                             {"account": label, "failures": len(streak), "since": first},
                             title_msg=server_msg("alert.syncFailing.title", title, account=label, count=len(streak)),
                             body_msg=server_msg("alert.syncFailing.body", body)))
    return out


# ---------------------------------------------------------------- the feed cards

def cards_of(con, cfg, ds) -> list[dict]:
    """The insights-feed cards for the whole household, as the web app builds them, but with the dismissed anomalies and price
    changes left out (the same persisted dismissals) and only the CONFIRMED price changes."""
    import dataclasses as dc
    from types import SimpleNamespace
    from coach.analytics import anomalies as an_mod, budgets as budgets_mod, forecast as forecast_mod, pricechanges, recurring
    from coach.api import views
    rec = recurring.detect_recurring(ds)
    dism = frozenset(r[0] for r in con.execute("SELECT id FROM anomalies WHERE dismissed_at IS NOT NULL"))
    anoms = an_mod.detect_anomalies(ds, None, rec, dism, False)
    pcs = pricechanges.price_changes(ds, None, rec, include_dismissed=True)
    pdis = pricechanges.dismissed_ids(con)
    changes = [dc.replace(c, dismissed=True) if c.id in pdis else c for c in pcs.changes]
    changes = [c for c in changes if not getattr(c, "dismissed", False) and c.confirmed and c.direction == "increase" and c.effect == "costs_more"]   # expense series only: never income (rent received)
    fc = forecast_mod.forecast(ds, 90, None, recurring=rec, points=True)
    bs = budgets_mod.budget_status(ds, recurring=rec)
    return views.build_insights(ds, SimpleNamespace(anomalies=anoms.anomalies), SimpleNamespace(changes=changes), fc, bs, rec)


def card_candidates(cards: list[dict], s: AlertSettings) -> list[Candidate]:
    out: list[Candidate] = []
    for c in cards:
        kind, sub, sev = c["kind"], c.get("subtype"), c["severity"]
        amount_c = _cents(c.get("amount"))
        payload = {"card": c["id"], "subtype": sub, "subject": c.get("subject"), "date": c.get("date"), "amount_c": amount_c,
                   "evidence": list(c.get("evidence") or [])[:10]}
        title, body = c["title"], c["body"]
        msgs = {"title_msg": c.get("title_msg"), "body_msg": c.get("body_msg")}
        if c.get("disclaimer"):
            payload["disclaimer"] = c["disclaimer"]
        if kind == "anomaly":
            if RANK.get(sev, 0) < RANK[s.anomaly_min_severity] or sev == "low":
                continue
            out.append(Candidate("unusual_charge", c["id"], sev, title, body, payload, **msgs))
        elif kind == "price_change":
            out.append(Candidate("price_increase", c["id"], "medium", title, body, payload, **msgs))
        elif kind == "forecast":
            if not c.get("date"):
                continue
            out.append(Candidate("low_balance", f"forecast:{c.get('subject')}:{sub}", sev, title, body, payload, **msgs))
        elif kind == "budget":
            out.append(Candidate("budget", f"budget:{c.get('subject')}:{c.get('date')}", sev, title, body, payload, **msgs))
        elif kind == "loan":
            if sub == "loa_end":
                out.append(Candidate("loa_end", c["id"], sev, title, body, payload, **msgs))
            elif sub == "loa_mileage":
                out.append(Candidate("loa_end", c["id"], sev, title, body, payload, **msgs))
            elif sub in ("extra_payment", "loa_mileage_missing"):
                continue                              # informational: the feed shows them, they do not alert
            else:                                    # missed_payment / amount_changed / wrong_account / capital_differs
                out.append(Candidate("loan_alert", c["id"], sev, title, body, payload, **msgs))
        elif kind == "rental":                           # E15: the scheme commitment, a rent that did not arrive, a declared limit exceeded
            key = {"scheme_end": "scheme_end", "rent_missing": "rent_missing", "rent_cap": "scheme_check", "tenant_income": "scheme_check"}.get(sub)
            if key:
                out.append(Candidate(key, c["id"], sev, title, body, payload, **msgs))
        elif kind == "subscription":
            if sub == "unused":
                out.append(Candidate("unused_subscription", f"sub-unused:{c.get('subject')}", sev, title, body, payload, **msgs))
            elif sub == "notice":
                out.append(Candidate("contract_notice", c["id"], sev, title, body, payload, **msgs))
            elif sub == "decision_check" and sev == "high":          # contradicted by the bank data (still charged)
                out.append(Candidate("decision_contradicted", c["id"], sev, title, body, payload, **msgs))
    return out


def all_candidates(con, cfg, ds, s: AlertSettings, now: Optional[dt.datetime] = None) -> list[Candidate]:
    out = consent_candidates(con, now) + sync_candidates(con, s.sync_failures) + card_candidates(cards_of(con, cfg, ds), s)
    try:                                                         # E12-4: LLM cost over the monthly threshold (local feed only)
        from coach.quality import usage as usage_mod
        c = usage_mod.candidate(con, cfg, now)
        out += [c] if c else []
    except Exception:                                            # noqa: BLE001  (the usage table is missing: migrations pending)
        pass
    try:                                                         # E14-6: a child's budget limits and unusual payments (local feed only)
        from coach.household import kidbudgets
        out += kidbudgets.candidates(ds)
    except Exception:                                            # noqa: BLE001  (no household / no people)
        pass
    seen, uniq = set(), []
    for c in out:                                    # one event per key (two cards can describe one situation)
        if c.id in seen:
            continue
        seen.add(c.id)
        uniq.append(c)
    return uniq
