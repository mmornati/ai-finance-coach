"""Upcoming payments calendar (E4-8): what happens in the next N days, from every source the coach knows.

Sources (``source`` of an item)
    recurring   next occurrences of the active recurring series (E4-3); an overdue one is placed today and says so.
                Internal transfers between own accounts are left out unless `include_transfers`.
    liability   monthly instalment of a memory liability that no recurring series explains (from its amortization schedule when
                computable, else the declared payment on the day of its start_date, until end_date), the END of every liability
                whose end_date is in the window (loan end, LOA end) and the reminder 6 months before a lease ends (E9-6).
    contract    renewal date, commitment end and the NOTICE DEADLINE (renewal / commitment end minus
                `notice_period_days`) of memory/contracts.
    consent     expiry of the bank consents (``sessions.valid_until``; an expired one is listed today).
    asset       reminder to refresh a manual asset value older than `asset_stale_months` (assets.yaml) - due on the day
                it becomes stale, or today when it already is or has no date.
Amounts are signed (money out negative) when known. ``days_until`` counts from `as_of`. ICS export: one all-day event
per item with a stable UID (deterministic: the same input gives the same file).
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.common import CoverageInfo, Result, add_months, money_str
from coach.analytics.dataset import Dataset
from coach.analytics.forecast import resolve_liability_account
from coach.analytics.recurring import RecurringResult, detect_recurring, occurrences
from coach.loans import service as loans_service

SOURCES = ("recurring", "liability", "contract", "consent", "asset")


@dataclass
class CalendarItem(Result):
    date: dt.date
    days_until: int
    source: str
    kind: str
    title: str
    amount_c: Optional[int]
    ref: str
    certainty: str                 # observed | scheduled | deadline | assumed
    account_label: Optional[str] = None
    note: Optional[str] = None


@dataclass
class CalendarResult(Result):
    as_of: dt.date
    days: int
    items: list
    counts: dict
    coverage: CoverageInfo
    evidence: list = field(default_factory=list)


def calendar_items(ds: Dataset, days: Optional[int] = None, recurring: Optional[RecurringResult] = None,
                   include_transfers: bool = False) -> CalendarResult:
    s = ds.settings
    days = days if days is not None else s.calendar_days
    today = ds.today
    end = today + dt.timedelta(days=days)
    rec = recurring or detect_recurring(ds)
    items: list[CalendarItem] = []

    def add(date, source, kind, title, amount, ref, certainty, account=None, note=None):
        items.append(CalendarItem(date, (date - today).days, source, kind, title, amount, ref, certainty, account, note))

    linked = set()
    for x in rec.series:
        if x.status != "active":
            continue
        linked.update(l.id for l in x.links if l.kind == "liability")
        if x.kind == "transfer" and not include_transfers:
            continue
        for dd, overdue in occurrences(x, today, end):
            if x.kind == "transfer":              # between own accounts: no payer / payee name, no account label (ICS-safe)
                add(dd, "recurring", "transfer", "Transfer between own accounts " + ("in" if x.direction == "in" else "out"),
                    x.expected_amount_c, x.id, "observed", None, x.cadence)
                continue
            add(dd, "recurring", "income" if x.direction == "in" else ("saving" if x.kind == "saving" else "payment"),
                x.entity, x.expected_amount_c, x.id, "observed", x.account_label,
                "overdue: expected earlier, not seen yet" if overdue else (x.cadence + (" (variable amount)" if x.amount_mode == "variable" else "")))
    for _rel, lb in ds.memory.liabilities:
        if lb.id not in linked and not (lb.end_date and lb.end_date < today):
            uid = resolve_liability_account(ds, lb)
            for dd, amount, cert, note in loans_service.upcoming_payments(ds, lb, today, end):
                add(dd, "liability", "loan_payment", lb.id, -amount, lb.id, cert, ds.label(uid) if uid else None, note)
        if lb.end_date and today <= lb.end_date <= end:
            add(lb.end_date, "liability", "loan_end", f"{lb.id} ends ({lb.kind})", None, lb.id, "scheduled", None,
                "end_date of the liability" + (" - residual value decision" if lb.kind in ("loa", "lld") else ""))
    for rem in loans_service.calendar_reminders(ds, today, end):          # E9-6: 6 months before a lease ends
        add(rem["date"], "liability", "loa_decision", rem["title"], None, rem["id"], "scheduled", None, rem["note"])
    from coach.skills import cancel as cancel_mod
    from coach.subs import usage as usage_mod
    from coach.subs.inventory import series_contracts
    smap = series_contracts(ds, rec)
    country = ds.memory.country or "FR"
    for _rel, c in ds.memory.contracts:
        label = c.provider or c.id
        amount = -int(round(c.billing.amount * 100)) if c.billing and c.billing.amount else None
        for date_, kind, text in ((c.renewal, "renewal", "renews"), (c.commitment_end, "commitment_end", "commitment ends")):
            if date_ is not None and today <= date_ <= end:
                add(date_, "contract", kind, f"{label} {text}", amount, c.id, "scheduled", None, None)
        # notice deadlines come from the rules engine (E8-3): the contract's own notice, the legal anniversary deadline, the day a
        # free cancellation opens
        try:
            res = cancel_mod.cancellability_of(c, today, country)
        except Exception:                                       # noqa: BLE001 - a calendar must not die on one odd contract
            res = {"rules": []}
        for dl in cancel_mod.notice_deadlines(c, res, today, end):
            if dl["kind"] == "contract_notice":
                add(dl["date"], "contract", "notice_deadline",
                    f"{label}: last day to give notice ({'renews' if dl['reference'] == 'renews' else 'commitment ends'} {dl['reference_date']})",
                    None, c.id, "deadline", None, f"{dl['days']} days before {dl['reference_date']}")
            elif dl["kind"] == "legal_notice":
                add(dl["date"], "contract", "notice_deadline", f"{label}: last day to give notice (anniversary {dl['reference_date']})",
                    None, c.id, "deadline", None, f"{dl['months']} months before the anniversary, unless the contract says otherwise - "
                                                  + "; ".join(dl["rules"]))
            else:
                add(dl["date"], "contract", "cancel_window_opens", f"{label}: free cancellation opens", None, c.id, "scheduled", None,
                    "; ".join(dl["rules"]))
        # E8-2: a reminder only from what the user recorded (last_used older than 60 days / 'never' while still paid)
        u = usage_mod.usage_of(c)
        series = next((x for x in rec.series if smap.get(x.id) == c.id), None)
        paying = None if series is None else series.status == "active"
        for sg in usage_mod.signals(u, paying=paying, today=today, last_payment=series.last_date if series else None):
            if sg["kind"] == "unused_60_days":
                add(today, "contract", "unused_reminder", f"{label}: unused for {sg['days']} days (last used {sg['last_used']}, as you recorded)",
                    None, c.id, "scheduled", None, "your own record; review whether to keep it")
            else:
                add(today, "contract", "never_used_reminder", f"{label}: you recorded 'never used' and it is still being paid", None, c.id,
                    "scheduled", None, "your own record; review whether to keep it")
    # E8-6: a decision whose effect the bank data has not confirmed yet
    from coach.subs import decisions as dec_mod
    for d in ds.decisions:
        v = dec_mod.verify(d, rec, today)
        who = d.name or d.contract_id or d.series_id
        ref = d.contract_id or d.series_id or d.id
        if v["status"] == "pending" and v["check_on"] and max(v["check_on"], today) <= end:
            add(max(v["check_on"], today), "contract", "decision_check", f"Check that '{who}' really {d.decision}", None, ref, "scheduled",
                None, "the recurring payments should have changed by now" if v["check_on"] >= today else "the bank data has not confirmed it yet")
        elif v["status"] == "contradicted":
            add(today, "contract", "decision_check", f"'{who}' was {d.decision} but is still being charged", None, ref, "deadline", None,
                v["reason"])
    for c in ds.consents:
        if c.valid_until is None:
            continue
        vu = c.valid_until[:10]
        try:
            dd = dt.date.fromisoformat(vu)
        except ValueError:
            continue
        if c.status in ("replaced",):
            continue
        if c.status in ("expired", "revoked"):
            add(today, "consent", "consent_expiry", f"{c.bank} bank consent {c.status}", None, c.session_id, "deadline",
                None, f"valid until {vu}")
        elif today <= dd <= end:
            add(dd, "consent", "consent_expiry", f"{c.bank} bank consent expires", None, c.session_id, "deadline",
                None, f"reconnect with `coach reconnect {c.bank}`")
    for a in ds.memory.assets:
        if a.amount is None:
            add(today, "asset", "asset_no_value", f"Enter the value of {a.id}", None, a.id, "scheduled", None, None)
        elif a.as_of is None:
            add(today, "asset", "asset_undated", f"Date the value of {a.id}", None, a.id, "scheduled", None, None)
        else:
            due = add_months(a.as_of, s.asset_stale_months)
            if due <= end:
                add(max(due, today), "asset", "asset_stale", f"Refresh the value of {a.id} (as of {a.as_of})", None,
                    a.id, "scheduled", None, f"older than {s.asset_stale_months} months" if due <= today else None)
    items.sort(key=lambda i: (i.date, SOURCES.index(i.source), i.kind, i.ref, i.title))
    counts = {src: sum(1 for i in items if i.source == src) for src in SOURCES}
    notes = [f"{len(ds.memory.warnings)} memory file problem(s): `coach memory check`"] if ds.memory.warnings else []
    cov = ds.coverage.info([], [], "forward-looking: recurring series from history, the rest from memory and consents",
                           notes)
    return CalendarResult(today, days, items, counts, cov, sorted({i.ref for i in items}))


# ---------------------------------------------------------------- ICS

def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    raw = line.encode()
    if len(raw) <= 75:
        return [line]
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > (75 if not out else 74):
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return [out[0]] + [" " + x for x in out[1:]]


def to_ics(res: CalendarResult) -> str:
    """RFC 5545 calendar, CRLF line endings, deterministic (DTSTAMP = the as-of date)."""
    stamp = res.as_of.strftime("%Y%m%d") + "T000000Z"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ai-finance-coach//calendar//EN", "CALSCALE:GREGORIAN"]
    for i in res.items:
        stable = i.kind in ("unused_reminder", "never_used_reminder", "decision_check")       # dated "today": the UID must not change daily
        uid = hashlib.sha1((f"{i.source}|{i.kind}|{i.ref}" if stable else f"{i.source}|{i.kind}|{i.ref}|{i.date}").encode()).hexdigest()[:20]
        desc = [f"source: {i.source}/{i.kind}", f"certainty: {i.certainty}"]
        if i.amount_c is not None:
            desc.insert(0, f"amount: {money_str(i.amount_c)} EUR")
        if i.account_label:
            desc.append(f"account: {i.account_label}")
        if i.note:
            desc.append(i.note)
        lines += ["BEGIN:VEVENT", f"UID:{uid}@ai-finance-coach", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{i.date.strftime('%Y%m%d')}",
                  f"DTEND;VALUE=DATE:{(i.date + dt.timedelta(days=1)).strftime('%Y%m%d')}",
                  f"SUMMARY:{_esc(i.title + (' ' + money_str(i.amount_c) + ' EUR' if i.amount_c is not None else ''))}",
                  f"DESCRIPTION:{_esc(chr(10).join(desc))}", "TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    out = []
    for ln in lines:
        out += _fold(ln)
    return "\r\n".join(out) + "\r\n"
