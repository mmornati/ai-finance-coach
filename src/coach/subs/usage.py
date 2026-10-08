"""Usage tracking (E8-2): how the household uses a service, as the USER records it in ``contracts/<id>.yaml`` ``usage``:

    usage: {frequency: daily|weekly|monthly|rarely|never|unknown, last_used: 2026-08-02, note: "kids watch it on Sundays"}

A plain text ``usage: "..."`` of an older file still loads (it counts as recorded, frequency unknown).

What is MEASURABLE, and what is not (nothing is invented):
    * ``unused_60_days``      the user-recorded ``last_used`` is more than 60 days ago and the service is still on file / paid;
    * ``paid_but_never_used`` the user marked the frequency ``never`` and the bank data shows the payments CONTINUE;
    * a service whose use leaves no trace in the bank data (software, streaming, a gym card...) has NO signal of its own: its
      usage is "unknown" until the user says; the coach asks (questions, never duplicated) and never guesses.
The reminders are computed from the user's own statements and the payments; they are information, not advice to cancel.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from coach.i18n_msg import server_msg
from coach.memory.schemas import USAGE_FREQUENCIES, Usage

UNUSED_DAYS = 60
NOT_MEASURABLE = ("Whether a service is used is not in the bank data: only what you record (frequency, last used) is "
                  "counted. Without it the usage stays unknown.")
NOT_MEASURABLE_MSG = server_msg("subs.usage.notMeasurable", NOT_MEASURABLE)
MEASURABLE_LAST_USED = server_msg("subs.usage.fromLastUsed", "from the last-used date you recorded")
MEASURABLE_NEVER = server_msg("subs.usage.neverAndPaying", "you recorded 'never' and the payments continue")


def usage_of(contract) -> dict:
    """Normalised view of a contract's usage: ``{frequency, last_used, note, recorded}``."""
    u = getattr(contract, "usage", None) if contract is not None else None
    if isinstance(u, Usage):
        recorded = u.frequency != "unknown" or u.last_used is not None or bool((u.note or "").strip())
        return {"frequency": u.frequency, "last_used": u.last_used, "note": u.note, "recorded": recorded}
    if isinstance(u, str) and u.strip():
        return {"frequency": "unknown", "last_used": None, "note": u.strip(), "recorded": True}
    return {"frequency": "unknown", "last_used": None, "note": None, "recorded": False}


def known_frequency(u: dict) -> bool:
    return u["frequency"] != "unknown"


def signals(u: dict, *, paying: Optional[bool], today: dt.date, last_payment: Optional[dt.date] = None) -> list[dict]:
    """The measurable signals of a recorded usage. ``paying``: True when the bank data shows the service is still paid, False
    when its payments stopped, None when the payments are not seen (a contract without a matching series)."""
    out: list[dict] = []
    if paying is False:
        return out                                   # the payments stopped: nothing to remind
    lu = u.get("last_used")
    if lu is not None:
        days = (today - lu).days
        if days > UNUSED_DAYS:
            out.append({"kind": "unused_60_days", "days": days, "last_used": lu, "measurable": MEASURABLE_LAST_USED["text"],
                        "measurable_msg": MEASURABLE_LAST_USED, "paying": paying})
    if u["frequency"] == "never" and paying:
        out.append({"kind": "paid_but_never_used", "last_payment": last_payment, "measurable": MEASURABLE_NEVER["text"],
                    "measurable_msg": MEASURABLE_NEVER})
    return out


def parse_frequency(v: str) -> str:
    v = (v or "").strip().lower()
    if v not in USAGE_FREQUENCIES:
        raise ValueError(f"frequency must be one of {', '.join(USAGE_FREQUENCIES)}")
    return v


def usage_value(frequency: str, last_used: Optional[dt.date], note: Optional[str]) -> dict:
    """The mapping written to the contract file (dates stay dates; empty parts are left out)."""
    v: dict = {"frequency": parse_frequency(frequency)}
    if last_used is not None:
        v["last_used"] = last_used
    if note and note.strip():
        v["note"] = note.strip()
    return v


def usage_question(cfg, x, fam, first, known, today: dt.date):
    """The usage question about one recurring series (the coach's ``questions_propose`` and the local generator share it, so
    the same key ``usage:<series>`` is never asked twice)."""
    from coach.memory import schemas
    from coach.memory.qgen import TOPIC_CODE, _person_like, qid, qmsg
    key = f"usage:{x.id}"
    name = x.entity
    person = _person_like(x.key or x.entity, None, set(), fam, first, known, cfg.llm_allowlist)
    if person:
        name = f"the {x.category.split('.')[-1].replace('_', ' ')} payment"
    monthly = abs(x.expected_amount_c) / 100 if x.cadence == "monthly" else round(x.yearly_cost_c / 1200, 2)
    link = next((l.id for l in x.links if l.kind == "contract"), None)
    target = {"file": f"contracts/{link}.yaml", "field": "usage"} if link else {"file": "contracts/"}
    text = (f"Do you still use {name} (about {monthly:.2f} EUR a month, {x.yearly_cost_c / 100:.2f} EUR a year, paid since "
            f"{x.first_date}; series {x.id})? How often, and by whom? Keep it, review it or stop it?")
    common = dict(monthly_amount=f"{monthly:.2f}", yearly_amount=f"{x.yearly_cost_c / 100:.2f}", since_date=x.first_date, series=x.id)
    # a name that may be a person's is never a param: the web says "the <category> payment" instead
    msg = (qmsg("question.usageUnnamed", text, payment_category=x.category, **common) if person else
           qmsg("question.usage", text, name=name, **common))
    return schemas.Question(
        id=qid("usage", key), topic="Subscriptions", topic_code=TOPIC_CODE["Subscriptions"], key=key, origin="coach", created=today,
        stake=round(x.yearly_cost_c / 100, 2), question=text, question_msg=msg,
        evidence={"series": x.id, "category": x.category, "monthly": monthly, "yearly": round(x.yearly_cost_c / 100, 2),
                  "since": x.first_date}, suggested_target=target)


def usage_questions(con, cfg, store, rec, today: dt.date, *, series_ids=None, limit: int = 10):
    """Usage questions for the active contract-like series whose usage is unknown and not yet asked (open, answered or
    dismissed). Returns ``(new questions, skipped_already_asked)``."""
    from coach.classify.candidates import household_names, known_merchants
    from coach.skills.subaudit import DISCRETIONARY, group_of
    fam, first = household_names(con)
    known = known_merchants(con)
    existing_keys = {q.key for q in store.questions() if q.key}
    existing_ids = {q.id for q in store.questions()}
    contracts = {m.id: m for _r, m in store.contracts()}
    out, skipped = [], 0
    cands = [x for x in rec.series if x.kind == "expense" and x.status == "active" and group_of(x.category) in DISCRETIONARY
             and (series_ids is None or x.id in series_ids)]
    cands.sort(key=lambda x: (-x.yearly_cost_c, x.id))
    for x in cands:
        cid = next((l.id for l in x.links if l.kind == "contract"), None)
        if cid and usage_of(contracts.get(cid))["recorded"]:
            continue
        q = usage_question(cfg, x, fam, first, known, today)
        if q.key in existing_keys or q.id in existing_ids:
            skipped += 1
            continue
        out.append(q)
        if len(out) >= limit:
            break
    return out, skipped
