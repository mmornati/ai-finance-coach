"""The savings tracker (E8-6): what the household decided about a recurring cost, and whether the bank data confirms it.

A decision: ``cancelled | renegotiated | switched | downgraded | kept``, the date, the monthly cost BEFORE and AFTER (cents), a
note and its source (``cli`` / ``ui`` / ``coach-llm``). Table ``decisions`` (migration 0015). The coach can only PROPOSE one
(``state = proposed``, source ``coach-llm``): it is not counted until the user confirms it (``coach subs decisions confirm`` in a
terminal, or the web app).

Verification (computed at read time from the recurring series; nothing is trusted from the note):
    cancelled / switched   VERIFIED when no payment of the series is dated after ``effective_on`` + 5 days (billing delay) and the
                           series has ended, or its next expected payment is more than 7 days overdue;
                           CONTRADICTED ("still paid") when a payment is dated after that;
    renegotiated / downgraded   VERIFIED when the latest payment on/after ``effective_on`` is, as a monthly amount, within 2 % (at
                           least 1 cent) of the recorded ``after``; CONTRADICTED when it is still about the old amount or moved to
                           another amount;
    kept                   nothing to verify, nothing saved;
    otherwise PENDING with a check date (``check_on``): a reminder is shown from that date on.

Realised savings count VERIFIED decisions only:
    monthly saving = before - after (cents);   since the decision = monthly saving x whole calendar months from ``effective_on``
    to today (a decision 40 days old has 1 month). Pending ones are reported apart as "claimed, not confirmed by the bank data".
"""
from __future__ import annotations

import datetime as dt
import secrets as _secrets
from dataclasses import dataclass
from typing import Optional

from coach.analytics.common import add_months, money_str
from coach.analytics.recurring import CADENCES
from coach.i18n_msg import server_msg
from coach.skills.money import cents

KINDS = ("cancelled", "renegotiated", "switched", "downgraded", "kept")
SOURCES = ("cli", "ui", "coach-llm")
STATES = ("proposed", "confirmed", "rejected")
ID_PREFIX = "dec_"
GRACE_DAYS = 5                    # a payment this long after the effective date is still a billing delay
OVERDUE_DAYS = 7                  # the next expected payment must be this overdue to call a cancellation verified
PRICE_TOLERANCE_PCT = 2
COLS = ("id", "contract_id", "series_id", "name", "decision", "decided_on", "effective_on", "before_monthly_c", "after_monthly_c",
        "note", "source", "state", "created_at", "confirmed_at")


class DecisionError(ValueError):
    pass


@dataclass
class Decision:
    id: str
    contract_id: Optional[str]
    series_id: Optional[str]
    name: Optional[str]
    decision: str
    decided_on: dt.date
    effective_on: Optional[dt.date]
    before_c: int
    after_c: int
    note: Optional[str]
    source: str
    state: str
    created_at: str
    confirmed_at: Optional[str]

    @property
    def effective(self) -> dt.date:
        return self.effective_on or self.decided_on

    @property
    def monthly_saving_c(self) -> int:
        return 0 if self.decision == "kept" else self.before_c - self.after_c


def _row(r) -> Decision:
    d = dict(zip(COLS, r))
    return Decision(d["id"], d["contract_id"], d["series_id"], d["name"], d["decision"], dt.date.fromisoformat(d["decided_on"]),
                    dt.date.fromisoformat(d["effective_on"]) if d["effective_on"] else None, d["before_monthly_c"],
                    d["after_monthly_c"], d["note"], d["source"], d["state"], d["created_at"], d["confirmed_at"])


def load(con, states=("confirmed",)) -> list[Decision]:
    ph = ",".join("?" * len(states))
    return [_row(r) for r in con.execute(f"SELECT {', '.join(COLS)} FROM decisions WHERE state IN ({ph}) "
                                         "ORDER BY decided_on DESC, id", tuple(states))]


def get(con, did: str) -> Optional[Decision]:
    r = con.execute(f"SELECT {', '.join(COLS)} FROM decisions WHERE id=?", (did,)).fetchone()
    return _row(r) if r else None


def _date(v, what: str) -> Optional[dt.date]:
    if v is None or isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v))
    except ValueError:
        raise DecisionError(f"{what} must be a date YYYY-MM-DD") from None


def check(*, decision: str, today: dt.date, contract_id: Optional[str] = None, series_id: Optional[str] = None,
          name: Optional[str] = None, decided_on=None, effective_on=None, before=None, after=None, note: Optional[str] = None,
          source: str = "cli") -> dict:
    """Validate a decision (nothing is stored). ``before`` / ``after``: monthly EUR amounts. A cancelled decision has after = 0; a
    kept one after = before. The coach source always yields a PROPOSED row."""
    if decision not in KINDS:
        raise DecisionError(f"decision must be one of {', '.join(KINDS)}")
    if source not in SOURCES:
        raise DecisionError(f"source must be one of {', '.join(SOURCES)}")
    if not (contract_id or series_id):
        raise DecisionError("say which subscription it is about (a contract id or a series id)")
    decided = _date(decided_on, "decided_on") or today
    eff = _date(effective_on, "effective_on")
    if decided > today:
        raise DecisionError("decided_on is in the future: record the decision once it is made (use effective_on for a later start)")
    if eff is not None and eff < decided:
        raise DecisionError("effective_on is before decided_on")
    if before is None:
        raise DecisionError("the monthly cost before the decision is required")
    before_c = cents(before)
    if before_c < 0:
        raise DecisionError("amounts must be >= 0")
    if decision == "cancelled":
        after_c = 0 if after is None else cents(after)
        if after_c != 0:
            raise DecisionError("a cancelled subscription costs 0 afterwards (use 'switched' for a replacement)")
    elif decision == "kept":
        after_c = before_c if after is None else cents(after)
        if after_c != before_c:
            raise DecisionError("a kept subscription costs the same afterwards")
    else:
        if after is None:
            raise DecisionError(f"the monthly cost after the decision is required for {decision}")
        after_c = cents(after)
        if after_c < 0:
            raise DecisionError("amounts must be >= 0")
        if after_c == before_c:
            raise DecisionError("before and after are equal: use 'kept' if nothing changes")
    note = " ".join((note or "").split())[:500] or None
    name = " ".join((name or "").split())[:80] or None
    return {"decision": decision, "contract_id": contract_id, "series_id": series_id, "name": name, "decided": decided, "effective": eff,
            "before_c": before_c, "after_c": after_c, "note": note, "source": source,
            "state": "proposed" if source == "coach-llm" else "confirmed"}


def add(con, **kw) -> Decision:
    """Validate (see :func:`check`) and store a decision."""
    v = check(**kw)
    state, decided, eff, before_c, after_c, note, name, source = (v["state"], v["decided"], v["effective"], v["before_c"], v["after_c"],
                                                                  v["note"], v["name"], v["source"])
    contract_id, series_id, decision = v["contract_id"], v["series_id"], v["decision"]
    did = ID_PREFIX + _secrets.token_hex(5)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con.execute("INSERT INTO decisions(id, contract_id, series_id, name, decision, decided_on, effective_on, before_monthly_c, "
                "after_monthly_c, note, source, state, created_at, confirmed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (did, contract_id, series_id, name, decision, decided.isoformat(), eff.isoformat() if eff else None, before_c, after_c,
                 note, source, state, now, now if state == "confirmed" else None))
    con.commit()
    return get(con, did)


def confirm(con, did: str) -> Decision:
    d = get(con, did)
    if d is None:
        raise DecisionError(f"no decision {did}")
    if d.state != "proposed":
        raise DecisionError(f"decision {did} is {d.state}, not proposed")
    con.execute("UPDATE decisions SET state='confirmed', confirmed_at=? WHERE id=?",
                (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), did))
    con.commit()
    return get(con, did)


def reject(con, did: str) -> Decision:
    d = get(con, did)
    if d is None:
        raise DecisionError(f"no decision {did}")
    if d.state != "proposed":
        raise DecisionError(f"decision {did} is {d.state}, not proposed")
    con.execute("UPDATE decisions SET state='rejected' WHERE id=?", (did,))
    con.commit()
    return get(con, did)


def remove(con, did: str) -> bool:
    n = con.execute("DELETE FROM decisions WHERE id=?", (did,)).rowcount
    con.commit()
    return n > 0


def count_proposed_by_coach(con) -> int:
    return con.execute("SELECT COUNT(*) FROM decisions WHERE source='coach-llm' AND state='proposed'").fetchone()[0]


# ---------------------------------------------------------------- verification

def linked_series(d: Decision, rec) -> list:
    if rec is None or not d.contract_id:
        return []
    return [s for s in rec.series if any(l.kind == "contract" and l.id == d.contract_id for l in s.links)]


def series_of(d: Decision, rec):
    """The recurring series a decision is about: its series id, else the series linked to its contract when there is exactly ONE (a
    contract that covers several series is ambiguous: see :func:`verify`)."""
    if rec is None:
        return None
    if d.series_id:
        x = next((s for s in rec.series if s.id == d.series_id), None)
        if x is not None:
            return x
    linked = linked_series(d, rec)
    return linked[0] if len(linked) == 1 else None


def whole_months(start: dt.date, today: dt.date) -> int:
    """Whole calendar months from ``start`` to ``today`` (0 if start is not past)."""
    if start > today:
        return 0
    n = 0
    while add_months(start, n + 1) <= today:
        n += 1
    return n


def monthly_equivalent_c(amount_c: int, cadence: str) -> int:
    return round(abs(amount_c) * CADENCES[cadence][3] / 12)


def _v(status: str, msg: dict, check_on, evidence) -> dict:
    """A verification: the English ``reason`` and its message ``reason_msg`` (docs/i18n.md "Server text"; the codes ``subs.decision.*``
    are shared with the reminder cards built on the same reason)."""
    return {"status": status, "reason": msg["text"], "reason_msg": msg, "check_on": check_on, "evidence": evidence}


def verify(d: Decision, rec, today: dt.date) -> dict:
    """-> ``{status: verified|pending|contradicted|not_applicable, reason, reason_msg, check_on, evidence}``."""
    eff = d.effective
    if d.decision == "kept":
        return _v("not_applicable", server_msg("subs.decision.kept", "nothing to verify: the subscription was kept"), None, None)
    x = series_of(d, rec)
    if x is None and len(linked_series(d, rec)) > 1:
        n = len(linked_series(d, rec))
        return _v("ambiguous", server_msg("subs.decision.ambiguous", f"the contract covers {n} recurring series: record the decision on one "
                                                                     "series (name it) so the bank data can be checked", count=n), None, None)
    if x is None:
        return _v("pending", server_msg("subs.decision.seriesNotFound",
                                        "the recurring series is not in the bank data (not found): confirm it by hand"),
                  eff + dt.timedelta(days=35), None)
    pays = [o for o in x.occurrences if o.amount_c < 0] if x.direction == "out" else list(x.occurrences)      # a refund (positive) is not a payment
    occ = [o for o in pays if o.date >= eff]
    if d.decision in ("cancelled", "switched"):
        late = [o for o in pays if o.date > eff + dt.timedelta(days=GRACE_DAYS)]
        if late:
            last = late[-1]
            amount = money_str(abs(last.amount_c))
            return _v("contradicted", server_msg("subs.decision.paymentsAfter", f"{len(late)} payment(s) after the effective date {eff}; latest "
                                                 f"{last.date} ({amount})", count=len(late), effective_date=eff, last_date=last.date,
                                                 last_amount=amount),
                      None, {"series": x.id, "last_payment": last.date, "payments_after": len(late)})
        overdue_from = (x.next_expected or x.last_date) + dt.timedelta(days=OVERDUE_DAYS)
        if eff <= today and (x.status == "ended" or today > overdue_from):
            msg = (server_msg("subs.decision.noPaymentSeriesEnded", f"no payment since {x.last_date} (the series ended)", last_date=x.last_date)
                   if x.status == "ended" else
                   server_msg("subs.decision.noPaymentNextOverdue", f"no payment since {x.last_date}, the next one is overdue", last_date=x.last_date))
            return _v("verified", msg, None, {"series": x.id, "last_payment": x.last_date})
        msg = (server_msg("subs.decision.nextNotOverdue", "no later payment yet, but the next one is not overdue") if eff <= today else
               server_msg("subs.decision.takesEffectLater", "the decision takes effect later"))
        return _v("pending", msg, max(eff, overdue_from) + dt.timedelta(days=1), {"series": x.id, "last_payment": x.last_date})
    # renegotiated / downgraded
    if not occ:
        return _v("pending", server_msg("subs.decision.noPaymentSinceEffective", "no payment on or after the effective date yet"),
                  max(eff, x.next_expected or eff) + dt.timedelta(days=OVERDUE_DAYS), {"series": x.id})
    last = occ[-1]
    m = monthly_equivalent_c(last.amount_c, x.cadence)
    tol = max(1, round(d.after_c * PRICE_TOLERANCE_PCT / 100))
    ev = {"series": x.id, "payment": last.date, "monthly_equivalent": money_str(m)}
    if abs(m - d.after_c) <= tol:
        return _v("verified", server_msg("subs.decision.asRecorded", f"the payment of {last.date} is {money_str(m)} a month, as recorded",
                                         payment_date=last.date, monthly_amount=money_str(m)), None, ev)
    if abs(m - d.before_c) <= max(1, round(d.before_c * PRICE_TOLERANCE_PCT / 100)):
        return _v("contradicted", server_msg("subs.decision.stillOldAmount",
                                             f"the payment of {last.date} is still about the old amount ({money_str(m)} a month)",
                                             payment_date=last.date, monthly_amount=money_str(m)), None, ev)
    return _v("contradicted", server_msg("subs.decision.otherAmount",
                                         f"the payment of {last.date} is {money_str(m)} a month, not the {money_str(d.after_c)} recorded",
                                         payment_date=last.date, monthly_amount=money_str(m), recorded_amount=money_str(d.after_c)), None, ev)


def counted_from(d: Decision, rec) -> dt.date:
    """Savings are counted from the first month AFTER the last payment made within the grace window (a service paid on the 8th of a month
    was still paid that month); otherwise from the effective date."""
    eff = d.effective
    if d.decision in ("cancelled", "switched"):
        x = series_of(d, rec)
        if x is not None:
            made = [o.date for o in x.occurrences if o.amount_c < 0 and o.date <= eff + dt.timedelta(days=GRACE_DAYS)]
            if made:
                last = max(made)
                first_next = dt.date(last.year + (last.month == 12), last.month % 12 + 1, 1)
                return max(eff, first_next)
    return eff


def status_row(d: Decision, rec, today: dt.date) -> dict:
    v = verify(d, rec, today)
    start = counted_from(d, rec)
    months = whole_months(start, today)
    saving = d.monthly_saving_c
    return {"id": d.id, "contract": d.contract_id, "series": d.series_id, "name": d.name, "decision": d.decision,
            "decided_on": d.decided_on, "effective_on": d.effective, "counted_from": start, "before_monthly": money_str(d.before_c),
            "after_monthly": money_str(d.after_c), "monthly_saving": money_str(saving), "months_counted": months,
            "since_decision": money_str(saving * months), "note": d.note, "source": d.source, "state": d.state,
            "status": v["status"], "reason": v["reason"], "reason_msg": v["reason_msg"], "check_on": v["check_on"], "evidence": v["evidence"],
            "reminder": bool((v["status"] == "pending" and v["check_on"] and today >= v["check_on"]) or v["status"] == "ambiguous"),
            "_saving_c": saving, "_since_c": saving * months}


def savings(decisions: list[Decision], rec, today: dt.date) -> dict:
    """Realised and claimed savings of the CONFIRMED decisions (hand-checkable: see the module docstring)."""
    rows = [status_row(d, rec, today) for d in decisions if d.state == "confirmed"]
    ver = [r for r in rows if r["status"] == "verified"]
    pend = [r for r in rows if r["status"] in ("pending", "ambiguous") and r["decision"] != "kept"]
    contra = [r for r in rows if r["status"] == "contradicted"]
    out = {"as_of": today,
           "realised_monthly": money_str(sum(r["_saving_c"] for r in ver if r["counted_from"] <= today)),
           "realised_since_decisions": money_str(sum(r["_since_c"] for r in ver)),
           "realised_yearly_run_rate": money_str(12 * sum(r["_saving_c"] for r in ver if r["counted_from"] <= today)),
           "verified": len(ver), "pending": len(pend), "contradicted": len(contra),
           "claimed_monthly_unverified": money_str(sum(r["_saving_c"] for r in pend)),
           "reminders": [r["id"] for r in rows if r["reminder"]],
           "decisions": rows,
           "note": SAVINGS_NOTE["text"], "note_msg": SAVINGS_NOTE}
    for r in rows:
        r.pop("_saving_c"), r.pop("_since_c")
    return out


SAVINGS_NOTE = server_msg("subs.savings.note", "only decisions the bank data confirms count as realised; pending ones are claims (a cancelled "
                                               "service still being paid is flagged). Months are whole calendar months from the effective date.")
