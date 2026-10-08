"""Infer the missing terms of a loan from its observed bank instalments (E9-1). Pure maths on numbers, no memory access.

What is solved. The instalment P is observed (median of the matched payments). An annuity ties four numbers together:
principal C, monthly rate r (= nominal % / 1200), number of instalments n and P: ``P = C r / (1 - (1+r)^-n)``. Given P and any TWO
of (C, r, n) the third follows (n = the term, known directly or from start_date + end_date):

    C unknown   C = P (1 - (1+r)^-n) / r                       (rounded to the euro)
    n unknown   n = -ln(1 - C r / P) / ln(1 + r)               (rounded to a whole month; the end date follows from start_date)
    r unknown   Newton's method on f(r) = C r / (1 - (1+r)^-n) - P      (rounded to 0.01 %)

A second route uses the capital still due B on a date (``outstanding`` + ``outstanding_as_of``) with the months left to the end date:
the same three equations on (B, r, months left) with the same instalment. ``start_date`` is derived from end_date and the term, or
(lower bound, low confidence) from the earliest matched payment.

Nothing is ever written: every result is a SUGGESTION marked ``inferred`` with a confidence and the assumptions, and the CLI / UI turn
it into a memory PROPOSAL the user accepts. An instalment that includes the borrower insurance overstates the capital and the rate:
the insurance is subtracted when it is known (``insurance.monthly``) and the confidence is capped when it is not.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.common import Result, add_months, money_str
from coach.i18n_msg import server_msg
from coach.loans import schedule as S
from coach.loans.payments import Payment
from coach.skills import loans as L

CONF = ("low", "medium", "high")
STABLE_SPREAD = 0.01                  # (max - min) / median of the recent instalments
MIN_OBS_HIGH, MIN_OBS_MEDIUM = 6, 3
RECENT = 12


def _cap(a: str, b: str) -> str:
    return CONF[min(CONF.index(a), CONF.index(b))]


@dataclass
class InferredField(Result):
    field: str                      # memory path: principal, rate.nominal, term_months, end_date, start_date, monthly_payment
    value: object                   # number, int or ISO date string
    confidence: str                 # low | medium | high
    method: str
    inferred: bool = True
    note: Optional[str] = None
    method_msg: Optional[dict] = None     # method and note for the web app (coach.i18n_msg)
    note_msg: Optional[dict] = None


@dataclass
class Inference(Result):
    id: str
    status: str                     # inferred | insufficient | not_applicable | nothing_to_infer
    observed: dict = field(default_factory=dict)
    known: dict = field(default_factory=dict)
    fields: list = field(default_factory=list)        # [InferredField]
    missing: list = field(default_factory=list)       # what would make an inference possible
    notes: list = field(default_factory=list)
    missing_msg: list = field(default_factory=list)   # the same, for the web app (same order)
    notes_msg: list = field(default_factory=list)

    def note(self, msg: dict) -> None:
        self.notes.append(msg["text"])
        self.notes_msg.append(msg)

    def set_missing(self, msg: dict) -> None:
        self.missing, self.missing_msg = [msg["text"]], [msg]


def _eur(x: float) -> str:
    """A capital in euros (float) as the decimal string of a message's ``*_amount`` param, rounded to the euro like the English text."""
    return money_str(int(round(x)) * 100)


# ---------------------------------------------------------------- the maths (floats: ratios, not money)

def annuity_payment(principal: float, r: float, n: int) -> float:
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)


def solve_principal(payment: float, r: float, n: int) -> float:
    return payment * n if r == 0 else payment * (1 - (1 + r) ** -n) / r


def solve_term(principal: float, payment: float, r: float) -> Optional[float]:
    if payment <= 0 or principal <= 0:
        return None
    if r == 0:
        return principal / payment
    x = 1 - principal * r / payment
    if x <= 0:
        return None                              # the instalment does not even cover the interest
    return -math.log(x) / math.log(1 + r)


def solve_rate_newton(principal: float, payment: float, n: int) -> Optional[float]:
    """Monthly rate r with annuity_payment(principal, r, n) == payment, by Newton's method (central-difference derivative);
    None when impossible (the instalments total less than the capital)."""
    if principal <= 0 or payment <= 0 or n <= 0:
        return None
    if payment * n < principal * (1 - 1e-12):
        return None
    if abs(payment * n - principal) <= 1e-9 * principal:
        return 0.0
    r = 0.003
    for _ in range(100):
        f = annuity_payment(principal, r, n) - payment
        h = max(r * 1e-6, 1e-10)
        df = (annuity_payment(principal, r + h, n) - annuity_payment(principal, max(r - h, 1e-12), n)) / (r + h - max(r - h, 1e-12))
        if df == 0:
            return None
        nr = r - f / df
        if nr <= 0:
            nr = r / 2
        if abs(nr - r) < 1e-13:
            r = nr
            break
        r = nr
    if abs(annuity_payment(principal, r, n) - payment) > 1e-6 * payment:
        return None
    return r


def rate_pct_of(r: float) -> float:
    return round(r * 1200, 2)


# ---------------------------------------------------------------- observed instalment

def observed_instalment(obs: list[Payment]) -> dict:
    recent = obs[-RECENT:]
    amts = [p.amount_c for p in recent]
    if not amts:
        return {"count": 0}
    med = int(statistics.median(amts))
    spread = (max(amts) - min(amts)) / med if med else 1.0
    return {"count": len(obs), "used": len(recent), "median_c": med, "latest_c": recent[-1].amount_c, "spread": round(spread, 4),
            "stable": spread <= STABLE_SPREAD, "first": obs[0].date, "last": obs[-1].date}


def infer_loan(lb, obs: list[Payment], today: dt.date) -> Inference:
    """Suggestions for the missing terms of the liability `lb` from its observed payments."""
    lb = S.lax(lb)
    res = Inference(lb.id, "insufficient")
    if lb.kind in ("loa", "lld"):
        res.status = "not_applicable"
        res.note(server_msg("infer.lease", "a lease has no amortization table to solve: record the rent, the end date and the residual value"))
        return res
    o = observed_instalment(obs)
    res.observed = {k: (money_str(v) if k.endswith("_c") else v) for k, v in o.items()}
    res.observed = {("median" if k == "median_c" else "latest" if k == "latest_c" else k): v for k, v in res.observed.items()}
    known = {"principal": lb.principal, "rate": lb.rate.nominal if lb.rate else None, "term": S.term_of(lb),
             "start_date": lb.start_date, "end_date": lb.end_date, "outstanding": lb.outstanding,
             "outstanding_as_of": lb.outstanding_as_of, "monthly_payment": lb.monthly_payment}
    res.known = {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in known.items() if v is not None}
    if o["count"] == 0:
        res.set_missing(server_msg("infer.missing.payments", "bank payments matched by payment_match (set payment_match or link the series)"))
        return res
    P_c = o["latest_c"] if not o["stable"] else o["median_c"]
    # base confidence from the observations
    conf = "high" if o["stable"] and o["count"] >= MIN_OBS_HIGH else ("medium" if o["stable"] and o["count"] >= MIN_OBS_MEDIUM else "low")
    if not o["stable"]:
        res.note(server_msg("infer.varies", f"the instalment varies by {o['spread']:.1%} over the last {o['used']} payments (variable rate, "
                            "modulation or a different debit): the latest amount is used and the confidence is low",
                            spread_pct=float(o["spread"]), count=o["used"]))
    # the insurance
    ins = lb.insurance
    ins_c = 0
    if ins is not None and ins.monthly is not None:
        ins_c = int(round(ins.monthly * 100))
        res.note(server_msg("infer.insuranceSubtracted", f"the borrower insurance ({money_str(ins_c)} a month) is assumed to be debited WITH "
                            "the instalment and is subtracted: if it is debited separately the figures below are too low",
                            insurance_amount=money_str(ins_c)))
    elif ins is not None and ins.rate_pct is not None and lb.principal is not None and ins.basis != "outstanding":
        ins_c = int(round(lb.principal * ins.rate_pct * 100 / 1200))
        res.note(server_msg("infer.insuranceRateSubtracted", "the insurance premium computed from its rate is assumed to be debited WITH the "
                            "instalment and is subtracted"))
    elif lb.kind in ("mortgage", "car_loan", "consumer_loan"):
        conf = _cap(conf, "medium")
        res.note(server_msg("infer.mayIncludeInsurance", "the instalment may include the borrower insurance (not recorded): the capital and "
                            "the rate inferred would then be a little too high"))
    if lb.rate is not None and lb.rate.type in ("variable", "mixed"):
        conf = _cap(conf, "medium")
        res.note(server_msg("infer.variableRate", "variable / mixed rate: the inferred rate is the one that fits the current instalment"))
    P = (P_c - ins_c) / 100
    if P <= 0:
        res.note(server_msg("infer.notLargerThanInsurance", "the instalment is not larger than the insurance"))
        return res
    fields: list[InferredField] = []

    def add(f, v, c, method: dict, note: Optional[dict] = None):
        fields.append(InferredField(f, v, c, method["text"], True, note and note["text"], method, note))

    # monthly_payment
    if lb.monthly_payment is None:
        add("monthly_payment", round(P_c / 100, 2), "high" if o["stable"] else "medium",
            server_msg("infer.method.median", "median of the observed bank payments") if o["stable"] else
            server_msg("infer.method.latest", "latest observed bank payment"))
    C = lb.principal
    r_pct = lb.rate.nominal if lb.rate and lb.rate.nominal is not None else None
    n = S.term_of(lb)
    r = None if r_pct is None else r_pct / 1200
    unknown = [x for x, v in (("principal", C), ("rate", r_pct), ("term", n)) if v is None]
    solved = False
    if len(unknown) == 1:
        u = unknown[0]
        if u == "principal":
            val = solve_principal(P, r, n)
            add("principal", int(round(val)), conf,
                server_msg("infer.method.principal", f"annuity: capital from the instalment, the rate {r_pct} % and {n} months",
                           rate=float(r_pct), count=int(n)),
                server_msg("infer.note.roundedEuro", "rounded to the euro; check against the contract"))
            solved = True
        elif u == "rate":
            sol = solve_rate_newton(C, P, n)
            if sol is None:
                res.note(server_msg("infer.noRateFits", "no rate fits: the instalments over the term total less than the capital (a wrong "
                                    "capital or term?)"))
            else:
                add("rate.nominal", rate_pct_of(sol), conf,
                    server_msg("infer.method.rate", f"Newton's method on the annuity of {C:,.0f} EUR over {n} months",
                               principal_amount=_eur(C), count=int(n)),
                    server_msg("infer.note.nominalRate", "nominal annual rate, excluding insurance"))
                solved = True
        else:
            m = solve_term(C, P, r)
            if m is None:
                res.note(server_msg("infer.noTermFits", "the instalment does not cover the interest on the capital: a wrong capital, rate or "
                                    "instalment"))
            else:
                mi = int(round(m))
                dev = abs(annuity_payment(C, r, mi) - P) / P
                c2 = conf if dev <= 0.01 else _cap(conf, "low")
                exact = round(m, 1)
                add("term_months", mi, c2,
                    server_msg("infer.method.term", f"annuity: months needed to repay {C:,.0f} EUR at {r_pct} % with this instalment",
                               principal_amount=_eur(C), rate=float(r_pct)),
                    server_msg("infer.note.exact", f"exact value {m:.1f}", value=exact) if dev <= 0.01 else
                    server_msg("infer.note.exactMismatch", f"exact value {m:.1f}; the rounded term does not reproduce the instalment within 1 %",
                               value=exact))
                if lb.start_date is not None and lb.end_date is None:
                    add("end_date", add_months(lb.start_date, mi).isoformat(), c2,
                        server_msg("infer.method.startPlusTerm", "start_date + the inferred term"))
                solved = True
    elif not unknown:
        res.note(server_msg("infer.allRecorded", "principal, rate and term are all recorded: nothing to infer (the schedule check compares them "
                            "with the payments)"))
    # second route: capital still due on a date and the months left
    if not solved and unknown and lb.outstanding and lb.outstanding_as_of and lb.outstanding > 0:
        B = lb.outstanding
        m_left = None
        if lb.end_date and lb.end_date > lb.outstanding_as_of:
            m_left = L.months_between(lb.outstanding_as_of, lb.end_date)
        c2 = _cap(conf, "medium")
        if r_pct is None and m_left:
            sol = solve_rate_newton(B, P, m_left)
            if sol is not None:
                add("rate.nominal", rate_pct_of(sol), c2,
                    server_msg("infer.method.rateOutstanding", f"Newton's method on the capital still due ({B:,.0f} EUR as of "
                               f"{lb.outstanding_as_of}) over the {m_left} months to end_date", outstanding_amount=_eur(B),
                               as_of_date=lb.outstanding_as_of, count=int(m_left)))
                solved = True
        elif r_pct is not None and m_left is None:
            m = solve_term(B, P, r)
            if m is not None:
                mi = int(round(m))
                add("end_date", add_months(lb.outstanding_as_of, mi).isoformat(), c2,
                    server_msg("infer.method.endOutstanding", f"annuity: {mi} months to repay {B:,.0f} EUR (as of {lb.outstanding_as_of}) at "
                               f"{r_pct} %", count=mi, outstanding_amount=_eur(B), as_of_date=lb.outstanding_as_of, rate=float(r_pct)))
                solved = True
        elif r_pct is not None and m_left and C is None:
            res.note(server_msg("infer.principalNotDerivable", "principal is not derivable from the remaining capital alone: record it from the "
                                "loan offer"))
    # start date: derived from the end date and the term only. The earliest payment in the bank data is NOT used: the history is
    # usually shorter than the loan, so it would be a bogus start date.
    if lb.start_date is None:
        end = lb.end_date
        term_v = n or next((f.value for f in fields if f.field == "term_months"), None)
        if end is not None and term_v:
            add("start_date", add_months(end, -int(term_v)).isoformat(), conf, server_msg("infer.method.endMinusTerm", "end_date - the term"))
    res.fields = fields
    terms = [f for f in fields if f.field != "monthly_payment"]       # the payment itself is observed, not inferred: not a "term"
    if terms:
        res.status = "inferred"
    elif not unknown:
        res.status = "nothing_to_infer"
    else:
        known_count = sum(1 for v in (C, r_pct, n) if v is not None)
        how_many = "none" if known_count == 0 else "only " + str(known_count)
        text = (f"two of principal, rate.nominal, end_date/term_months ({how_many} recorded) - or outstanding + "
                "outstanding_as_of + end_date for the remaining-capital route")
        res.set_missing(server_msg("infer.missing.twoOfNone", text) if known_count == 0 else
                        server_msg("infer.missing.twoOf", text, count=known_count))
    return res


def to_ops(inf: Inference, min_confidence: str = "low") -> list[dict]:
    """The memory operations (``set``) of the suggested fields, dates as dates (for ``coach memory`` proposals)."""
    ops = []
    for f in inf.fields:
        if CONF.index(f.confidence) < CONF.index(min_confidence):
            continue
        v = f.value
        if f.field.endswith("_date") and isinstance(v, str):
            v = dt.date.fromisoformat(v)
        ops.append({"op": "set", "path": f.field, "value": v})
    return ops
