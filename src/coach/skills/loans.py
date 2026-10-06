"""Loan maths for the mortgage-check and what-if skills (E7-8, E7-9). Deterministic, exact (integer cents, Decimal).

Conventions (stated in every result)
    * Annuity (constant instalment) loan, monthly compounding at nominal rate / 12. instalment = P r / (1 - (1+r)^-n),
      r = nominal % / 1200, rounded to the cent (half up). Interest of month k = balance x r rounded to the cent; the last
      instalment pays what is left, so the schedule always ends at exactly 0.
    * The first instalment is due ONE MONTH after ``start_date`` (instalment k is due ``start_date`` + k months, day clamped
      to the month end); ``term_months`` = whole months between ``start_date`` and ``end_date``. A loan with a deferral, a
      variable rate or a modulated instalment is not modelled: the result then differs from the bank's table, and
      ``payment_check`` says so when the declared instalment is known.
    * ``insurance.monthly`` is NOT part of the maths; the declared ``monthly_payment`` may or may not include it
      (``payment_check`` tries both).

Early repayment / renegotiation (FR): the indemnite de remboursement anticipe (IRA) of a home loan is capped by law at the
LOWER of six months of interest on the capital repaid (at the loan's rate) and 3 % of the capital remaining due. A renegotiation
with the SAME bank (avenant) has no IRA but may have fees; a rachat by another bank pays the IRA. IT: a surroga (portabilita)
of a mortgage has no penalty and no bank / notary costs for the borrower by law (Decreto Bersani-bis, art. 120-quater TUB).
Everything here is an ESTIMATE, not a quote: the bank's own figures decide.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Context, Decimal
from typing import Optional

from coach.analytics.common import Result, add_months, money_str
from coach import disclaimers as D
from coach.skills.money import ceil_div, cents, dec, pct_of_c, round_c

CTX = Context(prec=40)
IRA_CAP_PCT = Decimal(3)             # % of the capital remaining due
IRA_MONTHS = 6                       # months of interest
MAX_ROWS = 1200
DISCLAIMER = D.get("loan")      # the wording lives in coach.disclaimers (E11-5)


# ---------------------------------------------------------------- annuity

def monthly_rate(rate_pct) -> Decimal:
    return dec(rate_pct) / Decimal(1200)


def months_between(start: dt.date, end: dt.date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def annuity_c(principal_c: int, rate_pct, n: int) -> int:
    """The constant monthly instalment (cents) that repays `principal_c` over `n` months."""
    if n <= 0 or principal_c <= 0:
        raise ValueError("principal and term must be positive")
    r = monthly_rate(rate_pct)
    if r == 0:
        return round_c(Decimal(principal_c) / n)
    f = CTX.power(1 + r, Decimal(-n))
    return round_c(CTX.divide(CTX.multiply(Decimal(principal_c), r), 1 - f))


@dataclass
class Row(Result):
    k: int
    interest_c: int
    principal_c: int
    payment_c: int
    balance_c: int


def schedule(principal_c: int, rate_pct, n: int, payment_c: Optional[int] = None) -> list[Row]:
    """Month-by-month schedule of an annuity loan (the last row pays off the remainder)."""
    pay = payment_c if payment_c is not None else annuity_c(principal_c, rate_pct, n)
    r = monthly_rate(rate_pct)
    bal, rows = principal_c, []
    for k in range(1, n + 1):
        interest = round_c(Decimal(bal) * r)
        if k == n or pay - interest >= bal:
            princ, paid = bal, bal + interest
        else:
            princ, paid = pay - interest, pay
            if princ <= 0:
                raise ValueError("the instalment does not cover the interest")
        bal -= princ
        rows.append(Row(k, interest, princ, paid, bal))
        if bal <= 0:
            break
    return rows


def schedule_fixed_payment(capital_c: int, rate_pct, payment_c: int) -> list[Row]:
    """Schedule that keeps the instalment and lets the term shorten (a partial prepayment that keeps the payment)."""
    r = monthly_rate(rate_pct)
    if capital_c <= 0:
        return []
    if payment_c <= round_c(Decimal(capital_c) * r):
        raise ValueError("the instalment does not cover the interest")
    bal, rows = capital_c, []
    for k in range(1, MAX_ROWS + 1):
        interest = round_c(Decimal(bal) * r)
        if payment_c - interest >= bal:
            princ, paid = bal, bal + interest
        else:
            princ, paid = payment_c - interest, payment_c
        bal -= princ
        rows.append(Row(k, interest, princ, paid, bal))
        if bal <= 0:
            return rows
    raise ValueError("the loan is not repaid within 100 years")


def total_interest_c(rows: list[Row]) -> int:
    return sum(r.interest_c for r in rows)


def implied_rate_pct(outstanding_c: int, payment_c: int, months: int) -> Optional[Decimal]:
    """Nominal annual % that makes `payment_c` repay `outstanding_c` in `months` (bisection); None if impossible."""
    if outstanding_c <= 0 or payment_c <= 0 or months <= 0:
        return None
    if payment_c * months < outstanding_c:
        return None
    lo, hi = Decimal(0), Decimal(25)
    if annuity_c(outstanding_c, hi, months) < payment_c:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if annuity_c(outstanding_c, mid, months) < payment_c:
            lo = mid
        else:
            hi = mid
    return ((lo + hi) / 2).quantize(Decimal("0.01"))


# ---------------------------------------------------------------- facts of a liability of the memory

@dataclass
class LoanFacts:
    id: str
    kind: str
    principal_c: Optional[int] = None
    rate_pct: Optional[Decimal] = None
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None
    payment_c: Optional[int] = None
    outstanding_c: Optional[int] = None
    outstanding_as_of: Optional[dt.date] = None
    insurance_monthly_c: Optional[int] = None
    insurance_delegated: Optional[bool] = None
    penalty_clause: bool = False


def facts_of(lb) -> LoanFacts:
    """A memory ``Liability`` -> :class:`LoanFacts`."""
    ins = lb.insurance
    return LoanFacts(
        lb.id, lb.kind, cents(lb.principal) if lb.principal is not None else None,
        dec(lb.rate.nominal) if lb.rate and lb.rate.nominal is not None else None, lb.start_date, lb.end_date,
        cents(lb.monthly_payment) if lb.monthly_payment is not None else None,
        cents(lb.outstanding) if lb.outstanding is not None else None, lb.outstanding_as_of,
        cents(ins.monthly) if ins and ins.monthly is not None else None, ins.delegated if ins else None,
        lb.early_repayment_penalty not in (None, ""))


AMORT_FIELDS = (("principal", "principal_c"), ("rate.nominal", "rate_pct"), ("start_date", "start"), ("end_date", "end"))
OUTSTANDING_MAX_AGE_DAYS = 120


def missing_for_amortization(f: LoanFacts) -> list[str]:
    return [name for name, attr in AMORT_FIELDS if getattr(f, attr) is None]


def _payment_check(f: LoanFacts, theoretical_c: int) -> dict:
    if f.payment_c is None:
        return {"status": "no_declared_payment"}

    def close(a, b):
        return abs(a - b) <= max(100, int(0.01 * b))
    if close(f.payment_c, theoretical_c):
        return {"status": "matches", "declared": money_str(f.payment_c), "computed": money_str(theoretical_c)}
    if f.insurance_monthly_c is not None and close(f.payment_c - f.insurance_monthly_c, theoretical_c):
        return {"status": "matches_when_insurance_is_included", "declared": money_str(f.payment_c),
                "computed": money_str(theoretical_c)}
    return {"status": "differs", "declared": money_str(f.payment_c), "computed": money_str(theoretical_c),
            "hint": "variable rate, deferral, modulated instalments, fees or a wrong figure: check the loan table"}


def loan_state(f: LoanFacts, today: dt.date) -> dict:
    """What can be said about one loan today, and exactly what is missing. Never guesses a missing figure."""
    missing = missing_for_amortization(f)
    out: dict = {"id": f.id, "kind": f.kind, "missing_for_amortization": missing, "amortization": None,
                 "outstanding_declared": money_str(f.outstanding_c), "outstanding_as_of": f.outstanding_as_of,
                 "monthly_payment_declared": money_str(f.payment_c),
                 "insurance_monthly": money_str(f.insurance_monthly_c), "insurance_delegated": f.insurance_delegated}
    remaining_months = months_between(today, f.end) if f.end and f.end > today else (0 if f.end else None)
    out["remaining_months_to_end_date"] = remaining_months
    computed_c = None
    if not missing:
        n = months_between(f.start, f.end)
        if n <= 0:
            out["amortization"] = {"status": "invalid", "reason": "end_date is not after start_date"}
        else:
            rows = schedule(f.principal_c, f.rate_pct, n)
            made = sum(1 for r in rows if add_months(f.start, r.k) <= today)
            computed_c = rows[made - 1].balance_c if made else f.principal_c
            by_year: dict[int, list] = {}
            for r in rows:
                y = add_months(f.start, r.k).year
                acc = by_year.setdefault(y, [0, 0, 0])
                acc[0] += r.interest_c
                acc[1] += r.principal_c
                acc[2] += 1
            theo = annuity_c(f.principal_c, f.rate_pct, n)
            out["amortization"] = {
                "status": "computed", "term_months": n, "rate_pct": float(f.rate_pct), "payment_theoretical": money_str(theo),
                "first_instalment": add_months(f.start, 1), "last_instalment": add_months(f.start, len(rows)),
                "total_interest": money_str(total_interest_c(rows)), "payments_made": made,
                "remaining_instalments": len(rows) - made, "remaining_capital_computed": money_str(computed_c),
                "interest_paid_to_date": money_str(sum(r.interest_c for r in rows[:made])),
                "remaining_interest": money_str(sum(r.interest_c for r in rows[made:])),
                "by_year": [{"year": y, "interest": money_str(v[0]), "principal": money_str(v[1]), "instalments": v[2]}
                            for y, v in sorted(by_year.items())],
                "payment_check": _payment_check(f, theo),
                "assumptions": ["annuity loan, first instalment one month after start_date",
                                "no deferral, no variable rate, no modulated instalments"]}
    # the capital still due: the declared figure while it is recent, else the computed one
    age = (today - f.outstanding_as_of).days if f.outstanding_as_of else None
    used, source = None, None
    if f.outstanding_c is not None and age is not None and age <= OUTSTANDING_MAX_AGE_DAYS:
        used, source = f.outstanding_c, "declared (recent)"
    elif computed_c is not None:
        used, source = computed_c, "computed from the schedule"
    elif f.outstanding_c is not None:
        used, source = f.outstanding_c, "declared (date unknown or old)" if age is None else f"declared ({age} days old)"
    out["remaining_capital"] = money_str(used)
    out["remaining_capital_source"] = source
    out["_remaining_capital_c"] = used
    if f.outstanding_c is not None and computed_c is not None and computed_c > 0 and age is not None and age <= OUTSTANDING_MAX_AGE_DAYS:
        gap = abs(f.outstanding_c - computed_c)
        if gap > max(5000, 0.02 * computed_c):
            out["outstanding_check"] = {"status": "differs", "declared": money_str(f.outstanding_c),
                                        "computed": money_str(computed_c),
                                        "hint": "the table differs from the declared capital: the rate, the term or an early "
                                                "repayment may not be what the memory says"}
    # a missing rate can sometimes be BACK-SOLVED (flagged, never stored): outstanding + instalment + months to the end
    if f.rate_pct is None and used and f.payment_c and remaining_months:
        imp = implied_rate_pct(used, f.payment_c, remaining_months)
        if imp is not None:
            out["implied_rate"] = {"nominal_pct": float(imp), "approximate": True,
                                   "note": "back-solved from the capital, the instalment and the months to end_date; too high "
                                           "if the instalment includes insurance. Confirm the real rate before relying on it."}
    return out


# ---------------------------------------------------------------- renegotiation / rachat / surroga

@dataclass
class Renegotiation(Result):
    variant: str                      # renegotiation (same bank) | rachat (another bank) | surroga (IT)
    country: str
    remaining_capital_c: int
    remaining_months: int
    current_rate_pct: float
    new_rate_pct: float
    rate_gap_pts: float
    current_payment_c: int
    new_payment_c: int
    monthly_saving_c: int
    interest_current_c: int
    interest_new_c: int
    gross_interest_saving_c: int
    penalty_c: int
    penalty_basis: str
    bank_fees_c: int
    guarantee_fees_c: int
    other_fees_c: int
    total_costs_c: int
    net_saving_c: int
    break_even_months: Optional[int]
    rule_of_thumb_met: bool
    verdict: str
    notes: list = field(default_factory=list)
    disclaimer: str = DISCLAIMER


def ira_penalty_c(capital_c: int, rate_pct, months: int = IRA_MONTHS) -> tuple[int, str]:
    """FR home-loan IRA: the lower of 6 months of interest on the capital repaid and 3 % of the capital remaining due."""
    six = round_c(Decimal(capital_c) * dec(rate_pct) / Decimal(100) * months / Decimal(12))
    cap = pct_of_c(capital_c, IRA_CAP_PCT)
    return (six, f"6 months of interest ({money_str(six)}) is lower than 3 % of the capital ({money_str(cap)})") if six <= cap \
        else (cap, f"3 % of the capital ({money_str(cap)}) is lower than 6 months of interest ({money_str(six)})")


def renegotiation_estimate(*, current_rate_pct, new_rate_pct, remaining_capital, remaining_months: int, country: str = "FR",
                           variant: Optional[str] = None, bank_fees=0, guarantee_fees=0, other_fees=0,
                           penalty_override=None) -> Renegotiation:
    """Compare keeping the loan with a lower rate on the same remaining capital and months. Amounts in EUR (numbers)."""
    country = country.upper()
    if country not in ("FR", "IT"):
        raise ValueError("country must be FR or IT")
    variant = variant or ("surroga" if country == "IT" else "rachat")
    if variant not in ("renegotiation", "rachat", "surroga"):
        raise ValueError("variant must be renegotiation, rachat or surroga")
    if variant == "surroga" and country != "IT":
        raise ValueError("surroga is the Italian procedure: use country IT")
    cap = cents(remaining_capital)
    n = int(remaining_months)
    cur_pay = annuity_c(cap, current_rate_pct, n)
    new_pay = annuity_c(cap, new_rate_pct, n)
    int_cur = total_interest_c(schedule(cap, current_rate_pct, n, cur_pay))
    int_new = total_interest_c(schedule(cap, new_rate_pct, n, new_pay))
    notes: list[str] = []
    if penalty_override is not None:
        pen, basis = cents(penalty_override), "the amount given (from your contract)"
    elif variant == "rachat" and country == "FR":
        pen, basis = ira_penalty_c(cap, current_rate_pct)
        notes.append("IRA: Code de la consommation art. L313-47 (lower of 6 months of interest and 3 % of the capital remaining "
                     "due); some cases are exempt (death, professional mobility, ...): check your contract")
    elif variant == "surroga":
        pen, basis = 0, "no penalty by law for a surroga (Decreto Bersani-bis, art. 120-quater TUB)"
        notes.append("surroga: by law no penalty and no bank / notary costs for the borrower; the new bank may charge an "
                     "appraisal; check the offer")
    else:
        pen, basis = 0, "renegotiation with the same bank: no IRA, but an amendment fee is usual (give it in bank_fees)"
    bank, guar, oth = cents(bank_fees), cents(guarantee_fees), cents(other_fees)
    costs = pen + bank + guar + oth
    gross = int_cur - int_new
    net = gross - costs
    monthly = cur_pay - new_pay
    be = ceil_div(costs, monthly) if monthly > 0 and costs > 0 else (0 if monthly > 0 else None)
    gap = float(dec(current_rate_pct) - dec(new_rate_pct))
    rule = gap >= 0.7 and cap >= 7_000_000 and n >= 120
    if monthly <= 0:
        verdict = "no_saving"
    elif net <= 0:
        verdict = "costs_exceed_saving"
    elif be is not None and be > n:
        verdict = "break_even_after_the_loan_ends"
    elif rule:
        verdict = "worth_asking_for_quotes"
    else:
        verdict = "small_saving_check_the_costs"
    notes.append("rule of thumb quoted by brokers (not a recommendation): a gap of at least 0.7 point, a capital of at least "
                 "70,000 EUR and at least 10 years left")
    if variant != "surroga":
        notes.append("a new loan also resets the insurance and the guarantee: include them in the fees")
    return Renegotiation(variant, country, cap, n, float(dec(current_rate_pct)), float(dec(new_rate_pct)), round(gap, 4), cur_pay,
                         new_pay, monthly, int_cur, int_new, gross, pen, basis, bank, guar, oth, costs, net, be, rule, verdict, notes)


# ---------------------------------------------------------------- borrower insurance (Loi Lemoine)

@dataclass
class InsuranceDelegation(Result):
    country: str
    remaining_months: int
    current_monthly_c: int
    alternative_monthly_c: int
    monthly_saving_c: int
    yearly_saving_c: int
    total_saving_c: int
    fees_c: int
    net_saving_c: int
    verdict: str
    notes: list = field(default_factory=list)
    disclaimer: str = DISCLAIMER


def insurance_delegation_estimate(*, current_monthly, alternative_monthly, remaining_months: int, country: str = "FR",
                                  fees=0) -> InsuranceDelegation:
    """Saving of replacing the lender's group borrower insurance by an equivalent external policy."""
    country = country.upper()
    cur, alt, fee = cents(current_monthly), cents(alternative_monthly), cents(fees)
    n = int(remaining_months)
    if n <= 0:
        raise ValueError("remaining_months must be positive")
    monthly = cur - alt
    total = monthly * n
    notes = []
    if country == "FR":
        notes.append("Loi Lemoine (loi 2022-270 of 28 February 2022): the borrower insurance of a loan can be replaced at any time "
                     "without fees; the lender must accept an offer with equivalent guarantees and answer within 10 working days")
    else:
        notes.append("IT: the borrower may bring their own policy (DL 1/2012 art. 28, IVASS regulation 40/2018); the unused part "
                     "of a single premium is refundable on early closure; check equivalence with the bank")
    notes.append("the premium is often quoted on the initial capital: compare like with like (same guarantees, same basis)")
    verdict = "no_saving" if monthly <= 0 else ("fees_exceed_saving" if total - fee <= 0 else "saving")
    return InsuranceDelegation(country, n, cur, alt, monthly, monthly * 12, total, fee, total - fee, verdict, notes)


# ---------------------------------------------------------------- prepayment (what-if)

@dataclass
class Prepayment(Result):
    amount_c: int
    keep: str                          # payment (the term shortens) | term (the instalment falls)
    method: str                        # amortization | approximation
    approximate: bool
    capital_before_c: int
    capital_after_c: int
    payment_before_c: Optional[int]
    payment_after_c: Optional[int]
    monthly_effect_c: int              # change of the monthly outflow: negative = the household pays less
    months_saved: Optional[int]
    interest_saved_c: Optional[int]
    penalty_estimate_c: Optional[int]
    notes: list = field(default_factory=list)


def prepayment_effect(*, capital_c: int, amount_c: int, rate_pct, payment_c: Optional[int], remaining_months: Optional[int],
                      keep: str = "payment", ira_possible: bool = False) -> Prepayment:
    """A partial prepayment of `amount_c`. With a known rate: exact schedules before / after. Without: a zero-interest
    approximation, flagged."""
    if amount_c <= 0 or amount_c >= capital_c:
        raise ValueError("the prepayment must be positive and smaller than the capital still due")
    after = capital_c - amount_c
    notes: list[str] = []
    pen = None
    if ira_possible and rate_pct is not None:
        # six months of interest on the amount PREPAID at the loan rate, capped at 3 % of the capital due BEFORE the prepayment
        six = round_c(Decimal(amount_c) * dec(rate_pct) / Decimal(100) * IRA_MONTHS / Decimal(12))
        pen = min(six, pct_of_c(capital_c, IRA_CAP_PCT))
        notes.append("possible IRA on the amount prepaid (lower of 6 months of interest on it and 3 % of the capital due before the "
                     "prepayment): only if your contract applies it")
    if rate_pct is not None and remaining_months and remaining_months > 0:
        pay0 = payment_c or annuity_c(capital_c, rate_pct, remaining_months)
        base = schedule_fixed_payment(capital_c, rate_pct, pay0)
        if keep == "payment":
            rows = schedule_fixed_payment(after, rate_pct, pay0)
            return Prepayment(amount_c, keep, "amortization", False, capital_c, after, pay0, pay0, 0, len(base) - len(rows),
                              total_interest_c(base) - total_interest_c(rows), pen, notes)
        pay1 = annuity_c(after, rate_pct, len(base))
        rows = schedule(after, rate_pct, len(base), pay1)
        return Prepayment(amount_c, keep, "amortization", False, capital_c, after, pay0, pay1, pay1 - pay0, 0,
                          total_interest_c(base) - total_interest_c(rows), pen, notes)
    # no rate: linear / zero-interest approximation
    notes.append("the interest rate is unknown: the effect is approximated without interest; it understates the savings")
    if keep == "term":
        pay1 = None if payment_c is None else round_c(Decimal(payment_c) * Decimal(after) / Decimal(capital_c))
        return Prepayment(amount_c, keep, "approximation", True, capital_c, after, payment_c, pay1,
                          0 if payment_c is None else pay1 - payment_c, 0, None, pen, notes)
    saved = None if not payment_c else int(Decimal(amount_c) / Decimal(payment_c))
    return Prepayment(amount_c, keep, "approximation", True, capital_c, after, payment_c, payment_c, 0, saved, None, pen, notes)
