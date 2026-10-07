"""Subscription reminders for the insights feed (E8-2, E8-3, E8-6), computed from the memory contracts, the recurring series and
the confirmed decisions. Each reminder states what it is based on:

    unused          the user-recorded ``last_used`` is more than 60 days old, or the user recorded 'never' and the payments continue;
    notice          a notice deadline (the contract's own, or the legal anniversary deadline of the rules engine) within 30 days;
    decision_check  a decision the bank data has not confirmed (pending past its check date) or contradicts (still charged).

Nothing is inferred from silence: a service whose use leaves no trace in the bank data gets no 'unused' reminder.
"""
from __future__ import annotations

import datetime as dt
import hashlib

from coach.analytics.common import money_str
from coach.i18n_msg import server_msg, server_msg_or_none
from coach.skills import cancel as C
from coach.subs import decisions as DEC, usage as U

NOTICE_HORIZON_DAYS = 30


def _iid(*parts) -> str:
    return "ins_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def subscription_cards(ds, rec) -> list[dict]:
    today = ds.today
    country = ds.memory.country or "FR"
    from coach.subs.inventory import series_contracts
    smap = series_contracts(ds, rec)
    cards: list[dict] = []
    for _rel, c in ds.memory.contracts:
        label = c.provider or c.id
        series = next((x for x in rec.series if smap.get(x.id) == c.id), None)
        yearly = money_str(series.yearly_cost_c) if series is not None else None
        ev = [series.id] if series is not None else []
        u = U.usage_of(c)
        paying = None if series is None else series.status == "active"
        for sg in U.signals(u, paying=paying, today=today, last_payment=series.last_date if series else None):
            if sg["kind"] == "unused_60_days":
                title = f"{label}: unused for {sg['days']} days"
                body = (f"You recorded {sg['last_used']} as the last time you used it ({sg['days']} days ago). This comes from "
                        "your own record, not from the bank data. Worth reviewing whether to keep it.")
                cards.append({"id": _iid("sub-unused", c.id, sg["last_used"]), "kind": "subscription", "subtype": "unused", "severity": "medium",
                              "title": title, "title_msg": server_msg("reminder.unused.title", title, service=label, count=sg["days"]),
                              "body": body,
                              "body_msg": server_msg_or_none("reminder.unused.body", body, last_used_date=sg["last_used"], count=sg["days"]),
                              "amount": yearly, "date": today.isoformat(), "subject": label, "evidence": ev, "persist": "ui"})
            else:
                last = series.last_date if series else None
                title = f"{label}: marked 'never used' but still paid"
                body = (f"You recorded that you never use it, and the last payment was {last if last else '?'}. "
                        "Worth reviewing whether to keep it.")
                cards.append({"id": _iid("sub-never", c.id, last or ""), "kind": "subscription", "subtype": "unused",
                              "severity": "medium", "title": title, "title_msg": server_msg("reminder.neverUsed.title", title, service=label),
                              "body": body, "body_msg": server_msg("reminder.neverUsed.body", body, last_date=last),
                              "amount": yearly, "date": today.isoformat(), "subject": label,
                              "evidence": ev, "persist": "ui"})
        try:
            res = C.cancellability_of(c, today, country)
        except Exception:                                                  # noqa: BLE001
            continue
        for dl in C.notice_deadlines(c, res, today, today + dt.timedelta(days=NOTICE_HORIZON_DAYS)):
            if dl["kind"] == "window_opens":
                continue
            left = (dl["date"] - today).days
            title = f"{label}: last day to give notice in {left} days"
            body = (f"{dl['date']} is the last day to give notice before it {dl['reference'] if dl['kind'] == 'contract_notice' else 'renews on ' + str(dl['reference_date'])}"
                    f" ({dl['reference_date']}). Verify with your contract; nothing is cancelled by the coach.")
            bp = {"notice_date": dl["date"], "reference_date": dl["reference_date"]}
            if dl["kind"] != "contract_notice":
                body_msg = server_msg("reminder.notice.bodyAnniversary", body, **bp)
            elif dl["reference"] == "renews":
                body_msg = server_msg("reminder.notice.bodyRenews", body, **bp)
            elif dl["reference"] == "commitment ends":
                body_msg = server_msg("reminder.notice.bodyCommitmentEnds", body, **bp)
            else:
                body_msg = None
            cards.append({"id": _iid("sub-notice", c.id, dl["date"]), "kind": "subscription", "subtype": "notice",
                          "severity": "high" if left <= 14 else "medium", "title": title,
                          "title_msg": server_msg("reminder.notice.title", title, service=label, count=left),
                          "body": body, "body_msg": body_msg,
                          "amount": yearly, "date": dl["date"].isoformat(), "subject": label, "evidence": ev, "persist": "ui"})
    for d in ds.decisions:
        v = DEC.verify(d, rec, today)
        who = d.name or d.contract_id or d.series_id
        if v["status"] == "contradicted" or (v["status"] == "pending" and v["check_on"] and v["check_on"] <= today):
            contradicted = v["status"] == "contradicted"
            title = f"{who}: " + ("still charged after you " + d.decision if contradicted else "check that it really changed")
            # the body is the decision's own reason and its message (subs/decisions.py, codes subs.decision.*)
            cards.append({"id": _iid("sub-decision", d.id, v["status"]), "kind": "subscription", "subtype": "decision_check",
                          "severity": "high" if contradicted else "medium",
                          "title": title,
                          "title_msg": (server_msg_or_none(f"reminder.decision.stillCharged.{d.decision}", title, service=who) if contradicted
                                        else server_msg("reminder.decision.check", title, service=who)),
                          "body": v["reason"], "body_msg": v.get("reason_msg"),
                          "amount": money_str(d.monthly_saving_c * 12), "date": today.isoformat(), "subject": who,
                          "evidence": [d.series_id] if d.series_id else [], "persist": "ui"})
    return cards
