"""Early repayment, renegotiation and insurance scenarios on a real amortization schedule (E9-5). Estimates, never quotes.

Every figure comes from :mod:`coach.skills.loans` (``prepayment_effect``, ``renegotiation_estimate``, ``insurance_delegation_estimate``,
the IRA cap) applied to the capital and the number of instalments that REMAIN according to the loan's schedule (:mod:`coach.loans.schedule`),
so a deferral, a first instalment date or the insurance are respected. Nothing is estimated when the schedule is not computable: the result
lists the missing fields.

Prepayment, two options are always compared:
    keep_payment   the instalment stays, the loan ends earlier (months saved)
    keep_term      the end date stays, the instalment falls
For each: interest saved (exact schedules before / after), insurance saved (a flat premium for the months saved; a premium on the
outstanding capital recomputed on the lower balances), the penalty, the net saving = interest + insurance - penalty, and the break-even.
Penalty: FR home loan = the IRA (the lower of six months of interest on the amount prepaid and 3 % of the capital due before the
prepayment, only if the contract applies it); IT = none for a mortgage of a natural person for residential property (art. 120-ter TUB); FR consumer / car loan = the L312-34 cap (no
indemnity when the amount repaid over 12 months is at most 10,000 EUR, else at most 1 % of the amount repaid if more than a year is left, 0.5 % if
less); any other loan: the amount you give from the contract (`penalty`), else 0 with a note. Break-even: the first month whose cumulative interest saving (against the schedule without the
prepayment) covers the penalty; none when it never does.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Optional

from coach.analytics.common import add_months, money_str
from coach.i18n_msg import server_msg
from coach.loans import schedule as S
from coach.skills import loans as L
from coach.skills.money import cents, dec, round_c


def _needs(lb, sch: S.LoanSchedule, extra: Optional[list] = None) -> Optional[dict]:
    """`extra`: messages (:func:`server_msg`) of what else is missing."""
    if sch.status != "computed":
        note = server_msg("scenario.needsSchedule", "the amortization schedule cannot be computed: record these fields (or accept the "
                          "questions the coach proposes)")
        return {"status": "needs_fields", "missing": sch.missing, "missing_msg": sch.missing_msg, "alternative": sch.alternative,
                "alternative_msg": sch.alternative_msg, "note": note["text"], "note_msg": note}
    if extra:
        return _missing(extra)
    return None


def _missing(msgs: list) -> dict:
    return {"status": "needs_fields", "missing": [m["text"] for m in msgs], "missing_msg": msgs}


# the verdict of a prepayment option: a code (labels.loanVerdict.<code> on the web) and the English the CLI and the coach read
PREPAY_VERDICT = {"penalty_exceeds_saving": "the penalty exceeds what the interest and insurance save",
                  "saves_money": "saves money over the life of the loan"}
OPTION_LABEL = {"keep_payment": "keep the instalment, finish earlier", "keep_term": "keep the end date, lower the instalment"}


def _verdict(net_c: int) -> dict:
    code = "penalty_exceeds_saving" if net_c <= 0 else "saves_money"
    return {"verdict": PREPAY_VERDICT[code], "verdict_code": code}


def _remaining(sch: S.LoanSchedule, day: dt.date) -> tuple[int, list]:
    rows = [r for r in sch.rows if r.due >= day]            # a prepayment dated on an instalment day is applied before it
    cap = S.balance_on(sch, day - dt.timedelta(days=1))
    return (cap if cap is not None else 0), rows


def _insurance_total(lb, rows_before_balance: list[int], months: int) -> int:
    """Insurance over `months` instalments: flat monthly, or the rate on each balance in `rows_before_balance` (basis outstanding)."""
    ins = lb.insurance
    if ins is None:
        return 0
    if ins.monthly is not None:
        return cents(ins.monthly) * months
    if ins.rate_pct is not None and ins.basis == "outstanding":
        return sum(round_c(Decimal(b) * dec(ins.rate_pct) / Decimal(1200)) for b in rows_before_balance[:months])
    if ins.rate_pct is not None and lb.principal is not None:
        return round_c(Decimal(cents(lb.principal)) * dec(ins.rate_pct) / Decimal(1200)) * months
    return 0


CONSUMER_FREE_C = 1_000_000          # FR consumer credit (L312-34): no indemnity when the amount repaid over 12 months is at most 10,000 EUR
CONSUMER_LONG_PCT, CONSUMER_SHORT_PCT = Decimal(1), Decimal("0.5")


def _penalty(lb, country: str, capital_c: int, amount_c: int, rate, override, months_left: Optional[int] = None) -> tuple[int, str]:
    """(penalty in cents, its basis in English)."""
    pen, basis = _penalty_msg(lb, country, capital_c, amount_c, rate, override, months_left)
    return pen, basis["text"]


def _penalty_msg(lb, country: str, capital_c: int, amount_c: int, rate, override, months_left: Optional[int] = None) -> tuple[int, dict]:
    """(penalty in cents, its basis as a message: the English text for the CLI and the coach, the code for the web). The legal
    citations stay word for word in every language."""
    if override is not None:
        return cents(override), server_msg("scenario.penalty.override", "the amount you gave (from your contract)")
    if lb.kind == "mortgage" and country == "FR":
        six = round_c(Decimal(amount_c) * dec(rate) / Decimal(100) * L.IRA_MONTHS / Decimal(12))
        cap = round_c(Decimal(capital_c) * L.IRA_CAP_PCT / Decimal(100))
        pen = min(six, cap)
        return pen, server_msg("scenario.penalty.ira", f"IRA (Code de la consommation L313-47): the lower of six months of interest on the amount "
                               f"prepaid ({money_str(six)}) and 3 % of the capital due before the prepayment ({money_str(cap)}); your contract may "
                               "waive it, or apply it only to some cases", six_months_amount=money_str(six), cap_amount=money_str(cap))
    if lb.kind == "mortgage" and country == "IT":
        return 0, server_msg("scenario.penalty.itMortgage", "no penalty by law for the early repayment of a mortgage of a natural person for the "
                             "purchase or renovation of residential property (art. 120-ter TUB, from the Bersani decree): check your contract")
    if lb.kind in ("car_loan", "consumer_loan") and country == "FR":
        if amount_c <= CONSUMER_FREE_C:
            return 0, server_msg("scenario.penalty.frConsumerFree", "FR consumer credit (Code de la consommation L312-34): no indemnity when the "
                                 "amount repaid over 12 months is at most 10,000 EUR; earlier prepayments of the same 12 months add up: give the "
                                 "penalty of your contract if they do", free_amount=money_str(CONSUMER_FREE_C))
        pct = CONSUMER_LONG_PCT if (months_left or 0) > 12 else CONSUMER_SHORT_PCT
        pen = round_c(Decimal(amount_c) * pct / Decimal(100))
        if pct == 1:
            return pen, server_msg("scenario.penalty.frConsumerLong", f"FR consumer credit (L312-34): at most {pct} % of the amount repaid (more "
                                   f"than a year of the contract is left) = {money_str(pen)}; your contract may waive it",
                                   penalty_amount=money_str(pen))
        return pen, server_msg("scenario.penalty.frConsumerShort", f"FR consumer credit (L312-34): at most {pct} % of the amount repaid (less "
                               f"than a year of the contract is left) = {money_str(pen)}; your contract may waive it",
                               penalty_amount=money_str(pen))
    return 0, server_msg("scenario.penalty.other", "no legal penalty is modelled for this kind of loan: give the amount of your contract in "
                         "`penalty`")


def _break_even(base_rows: list, new_rows: list, penalty_c: int) -> Optional[int]:
    """The first month whose cumulative interest saving covers the penalty (0 without a penalty; None when it never does)."""
    if penalty_c <= 0:
        return 0
    cum = 0
    for k, b in enumerate(base_rows):
        cum += b.interest_c - (new_rows[k].interest_c if k < len(new_rows) else 0)
        if cum >= penalty_c:
            return k + 1
    return None


def prepay(lb, sch: S.LoanSchedule, today: dt.date, amount, on: Optional[dt.date] = None, country: str = "FR",
           penalty=None) -> dict:
    lb = S.lax(lb)
    n = _needs(lb, sch)
    if n:
        return n
    day = max(on or today, today)
    capital_c, rows = _remaining(sch, day)
    amount_c = cents(amount)
    if not rows:
        m = server_msg("scenario.nothingLeft", "no instalment remains after that date")
        return {"status": "nothing_left", "note": m["text"], "note_msg": m}
    if amount_c <= 0:
        raise ValueError("the amount must be positive")
    if amount_c >= capital_c:
        m = server_msg("scenario.payoff", "the amount covers the whole capital: ask the lender for a payoff quote (capital + accrued interest "
                       "+ penalty)")
        return {"status": "payoff", "capital_due": money_str(capital_c), "note": m["text"], "note_msg": m}
    if any(r.kind == "deferral" for r in rows):
        return _missing([server_msg("scenario.deferralNotModelled", "a prepayment during a deferral is not modelled: ask the lender")])
    rate = lb.rate.nominal
    pay0 = sch.payment_c
    pen_c, pen_basis = _penalty_msg(lb, country, capital_c, amount_c, rate, penalty, len(rows))
    out: dict = {"status": "computed", "country": country, "date": day.isoformat(), "amount": money_str(amount_c),
                 "capital_before": money_str(capital_c), "capital_after": money_str(capital_c - amount_c),
                 "remaining_instalments": len(rows), "instalment": money_str(pay0), "penalty": money_str(pen_c),
                 "penalty_basis": pen_basis["text"], "penalty_basis_msg": pen_basis, "approximate": sch.approximate, "options": [],
                 "notes": list(sch.assumptions), "notes_msg": list(sch.assumptions_msg)}
    base_rows = L.schedule_fixed_payment(capital_c, rate, pay0)
    base_interest = L.total_interest_c(base_rows)
    base_before = [r.balance_c + r.principal_c for r in base_rows]
    # -- keep the instalment: the loan ends earlier
    kp_rows = L.schedule_fixed_payment(capital_c - amount_c, rate, pay0)
    saved_months = len(base_rows) - len(kp_rows)
    int_saved = base_interest - L.total_interest_c(kp_rows)
    ins_before = _insurance_total(lb, base_before, len(base_rows))
    ins_after = _insurance_total(lb, [r.balance_c + r.principal_c for r in kp_rows], len(kp_rows))
    be = _break_even(base_rows, kp_rows, pen_c)
    new_last = add_months(rows[0].due, len(kp_rows) - 1) if rows else None
    out["options"].append({
        "mode": "keep_payment", "label": OPTION_LABEL["keep_payment"], "new_instalment": money_str(pay0), "monthly_change": "0.00",
        "months_saved": saved_months, "new_last_instalment": new_last.isoformat() if new_last else None,
        "interest_saved": money_str(int_saved), "insurance_saved": money_str(ins_before - ins_after),
        "penalty": money_str(pen_c), "net_saving": money_str(int_saved + ins_before - ins_after - pen_c),
        "break_even_months": 0 if pen_c == 0 else be, **_verdict(int_saved + ins_before - ins_after - pen_c)})
    # -- keep the end date: the instalment falls
    pay1 = L.annuity_c(capital_c - amount_c, rate, len(base_rows))
    kt_rows = L.schedule(capital_c - amount_c, rate, len(base_rows), pay1)
    int_saved2 = base_interest - L.total_interest_c(kt_rows)
    ins_after2 = _insurance_total(lb, [r.balance_c + r.principal_c for r in kt_rows], len(kt_rows))
    net2 = int_saved2 + ins_before - ins_after2 - pen_c
    be2 = _break_even(base_rows, kt_rows, pen_c)
    out["options"].append({
        "mode": "keep_term", "label": OPTION_LABEL["keep_term"], "new_instalment": money_str(pay1),
        "monthly_change": money_str(pay1 - pay0), "months_saved": 0, "new_last_instalment": rows[-1].due.isoformat(),
        "interest_saved": money_str(int_saved2), "insurance_saved": money_str(ins_before - ins_after2), "penalty": money_str(pen_c),
        "net_saving": money_str(net2), "break_even_months": be2, **_verdict(net2)})
    if lb.insurance is not None and lb.insurance.monthly is not None:
        m = server_msg("scenario.flatInsurance", "a flat insurance premium is not reduced by a partial prepayment unless the lender recalculates "
                       "it: only the months saved (keep_payment) change what is paid in insurance")
        out["notes"].append(m["text"])
        out["notes_msg"].append(m)
    # the web shows the sentence in its language and the loan disclaimer (disclaimers.py, key `disclaimer_key`) from GET /meta/disclaimers
    m = server_msg("scenario.estimate", "estimate from the stored schedule at the current nominal rate; the lender's table decides. "
                   + L.DISCLAIMER)
    out["notes"].append(m["text"])
    out["notes_msg"].append(m)
    out["disclaimer_key"] = "loan"
    return out


def renegotiate(lb, sch: S.LoanSchedule, today: dt.date, new_rate, country: str = "FR", variant: Optional[str] = None,
                bank_fees=0, guarantee_fees=0, other_fees=0, penalty=None) -> dict:
    lb = S.lax(lb)
    n = _needs(lb, sch)
    if n:
        return n
    cap = sch.remaining_capital_c
    months = sch.remaining_instalments
    if not months or cap is None or cap <= 0:
        return {"status": "nothing_left"}
    r = L.renegotiation_estimate(current_rate_pct=lb.rate.nominal, new_rate_pct=new_rate, remaining_capital=Decimal(cap) / 100,
                                 remaining_months=months, country=country, variant=variant, bank_fees=bank_fees,
                                 guarantee_fees=guarantee_fees, other_fees=other_fees, penalty_override=penalty)
    return {"status": "computed", "basis": {"capital": money_str(cap), "remaining_instalments": months,
                                            "current_rate_pct": float(dec(lb.rate.nominal)), "from": "the amortization schedule"},
            **r.to_dict()}


def insurance(lb, sch: S.LoanSchedule, today: dt.date, alternative_monthly, country: str = "FR", fees=0) -> dict:
    lb = S.lax(lb)
    if sch.status != "computed" or not sch.rows:
        return _needs(lb, sch) or _missing([server_msg("scenario.missingSchedule", "schedule")])
    nxt = next((r for r in sch.rows if not r.made), None)
    if nxt is None:
        return {"status": "nothing_left"}
    if nxt.insurance_c <= 0:
        return _missing([server_msg("scenario.missingInsurance", "insurance.monthly (or insurance.rate_pct and basis)")])
    r = L.insurance_delegation_estimate(current_monthly=Decimal(nxt.insurance_c) / 100, alternative_monthly=alternative_monthly,
                                        remaining_months=sch.remaining_instalments, country=country, fees=fees)
    return {"status": "computed", "basis": {"current_monthly_from": "the next instalment of the schedule",
                                            "remaining_instalments": sch.remaining_instalments}, **r.to_dict()}


def insight_text(kind: str, lb_kind: str, res: dict) -> tuple[str, str]:
    """(title, markdown body) of a scenario result stored as an insight on request. No loan id, no lender name."""
    label = {"prepay": "Early repayment", "renegotiate": "Renegotiation", "insurance": "Insurance change"}[kind]
    title = f"{label} scenario ({lb_kind.replace('_', ' ')})"
    lines = [f"**{label}** estimate on the stored amortization schedule. Estimates only, not an offer."]
    if kind == "prepay":
        lines.append(f"Amount {res['amount']} EUR on {res['date']}, capital due {res['capital_before']} EUR, penalty {res['penalty']} EUR "
                     f"({res['penalty_basis']}).")
        for o in res["options"]:
            lines.append(f"- {o['label']}: instalment {o['new_instalment']} EUR, {o['months_saved']} month(s) saved, interest saved "
                         f"{o['interest_saved']} EUR, insurance saved {o['insurance_saved']} EUR, net saving {o['net_saving']} EUR"
                         + (f", break-even {o['break_even_months']} month(s)" if o["break_even_months"] is not None else "") + ".")
    elif kind == "renegotiate":
        lines.append(f"{res['variant']} at {res['new_rate_pct']} % instead of {res['current_rate_pct']} %: monthly saving "
                     f"{res['monthly_saving']} EUR, gross interest saving {res['gross_interest_saving']} EUR, costs {res['total_costs']} EUR "
                     f"(penalty {res['penalty']} EUR), net saving {res['net_saving']} EUR, break-even "
                     f"{res['break_even_months'] if res['break_even_months'] is not None else 'never'} month(s): {res['verdict']}.")
    else:
        lines.append(f"Insurance {res['current_monthly']} -> {res['alternative_monthly']} EUR a month over {res['remaining_months']} months: "
                     f"net saving {res['net_saving']} EUR ({res['verdict']}).")
    lines.append("General information, not financial advice.")
    return title, "\n\n".join(lines)
