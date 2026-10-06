"""Renegotiate-or-sell indicators of a rental property (E15-5): facts and their neutral reading, never a recommendation.

    * the loan rate against the market rate THE OWNER ENTERED (``market_rate`` of the property or an argument; nothing is looked up). A rate
      more than ``[analytics] rental_rate_gap_pts`` above it is flagged "worth a quote"; the renegotiation scenario of E9-5 prices it on the real
      amortization schedule (costs, penalty, break-even) when the schedule is computable;
    * the end of the commitment period (E15-3);
    * the net equity: the valuation the owner declared (``value`` with ``as_of``) less the capital still due from the schedule (else the declared
      outstanding; an unknown loan makes the equity unknown, it is never estimated). The equity is BEFORE selling costs, the early-repayment
      penalty and any tax on a capital gain, none of which are modelled.

A market rate older than 30 days (or undated) is flagged. No lender, product or insurer is ever named or advised.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from coach import disclaimers as D
from coach.analytics.common import add_months, div_cents, to_cents
from coach.loans import scenario as SCN, service as LS
from coach.rental import cashflow as CF, scheme as SC

MARKET_MAX_AGE_DAYS = 30
GENERAL = D.GENERAL_ADVICE["en"]


def market_of(prop, rate_pct: Optional[float], rate_date: Optional[dt.date], today: dt.date) -> dict:
    """The market rate in use: an argument wins over the one recorded on the property. Its age is checked."""
    mr = getattr(prop.asset, "market_rate", None)
    if rate_pct is not None:
        rate, day, src = rate_pct, rate_date, "given for this call"
    elif mr is not None:
        rate, day, src = mr.rate_pct, mr.as_of, "recorded on the property"
    else:
        return {"status": "missing", "note": "enter the current market rate for a loan of similar duration (your own quote or a comparator you "
                                             "looked at, with its date): the coach never looks it up"}
    out = {"status": "given", "rate_pct": rate, "date": day, "basis": src}
    if day is None:
        out["warning"] = "the date of the market rate was not given: treat it as possibly outdated"
    else:
        age = (today - day).days
        out["age_days"] = age
        if age > MARKET_MAX_AGE_DAYS:
            out["warning"] = f"the market rate is {age} days old (more than {MARKET_MAX_AGE_DAYS}): do not present it as current"
        elif age < 0:
            out["warning"] = "the date of the market rate is in the future"
    return out


def _capital(ds, lb) -> dict:
    sch = LS.schedule_of(ds, lb)
    if sch.status == "computed" and sch.remaining_capital_c is not None:
        return {"capital_c": sch.remaining_capital_c, "source": "amortization schedule", "months_left": sch.remaining_instalments,
                "approximate": sch.approximate}
    if lb.outstanding is not None:
        return {"capital_c": to_cents(lb.outstanding), "source": f"declared outstanding as of {lb.outstanding_as_of or 'an unknown date'}",
                "months_left": None, "approximate": True}
    return {"capital_c": None, "source": "unknown", "missing": sch.missing or ["outstanding or the loan terms"]}


def equity(ds, prop) -> dict:
    a = prop.asset
    out: dict = {"status": "unknown"}
    value = getattr(a, "value", None)
    if value is None:
        out["missing"] = ["value (the valuation you believe, with as_of)"]
    else:
        out.update(value_c=to_cents(value), value_as_of=getattr(a, "as_of", None))
        cut = add_months(ds.today, -ds.settings.asset_stale_months)
        if getattr(a, "as_of", None) is None:
            out["value_warning"] = "the valuation has no as_of date"
        elif a.as_of < cut:
            out["value_warning"] = f"the valuation dates from {a.as_of}: update it"
    if not prop.loans:
        out.setdefault("missing", []).append("the loan (link it with `loan`), or record that there is none")
        return out
    caps = [_capital(ds, lb) for lb in prop.loans]
    out["loans"] = [{"capital_c": c["capital_c"], "source": c["source"], "months_left": c.get("months_left")} for c in caps]
    if any(c["capital_c"] is None for c in caps):
        out["missing"] = out.get("missing", []) + ["the capital still due of a loan (its terms or a declared outstanding)"]
        return out
    owed = sum(c["capital_c"] for c in caps)
    out["outstanding_c"] = owed
    out["approximate"] = any(c.get("approximate") for c in caps)
    if value is not None:
        v = to_cents(value)
        out.update(status="computed", net_equity_c=v - owed, loan_to_value_pct=round(owed / v * 100, 1) if v else None,
                   equity_share_pct=round((v - owed) / v * 100, 1) if v else None)
        out["notes"] = ["before the selling costs, the early-repayment penalty and any tax on a capital gain: none of them is modelled"]
    return out


def rate_check(ds, prop, market: dict, fees: Optional[dict] = None) -> dict:
    out: dict = {"status": "unknown"}
    lbs = [lb for lb in prop.loans if lb.kind == "mortgage" or lb.kind == "consumer_loan" or lb.rate]
    if not lbs:
        out["missing"] = ["the loan and its nominal rate"]
        return out
    lb = lbs[0]
    rate = lb.rate.nominal if lb.rate and lb.rate.nominal is not None else None
    if rate is None:
        out["missing"] = ["rate.nominal of the loan"]
        return out
    out.update(loan_rate_pct=rate, variable=bool(lb.rate and lb.rate.type in ("variable", "mixed")))
    if market.get("status") != "given":
        out["missing"] = ["a market rate you entered"]
        return out
    gap = round(rate - market["rate_pct"], 2)
    thr = ds.settings.rental_rate_gap_pts
    out.update(market_rate_pct=market["rate_pct"], gap_pts=gap, threshold_pts=thr,
               status="above_market" if gap >= thr else "close_to_market" if gap > -thr else "below_market")
    out["reading"] = (f"the loan rate is {gap:.2f} point(s) above the market rate you entered: a quote may be worth asking for. {GENERAL}"
                      if gap >= thr else "the loan rate is close to the market rate you entered: little to gain from a renegotiation on the rate alone"
                      if gap > -thr else "the loan rate is below the market rate you entered: a renegotiation would raise it")
    if gap >= thr:
        sch = LS.schedule_of(ds, lb)
        fees = fees or {}
        country = ds.memory.country or "FR"
        try:
            res = SCN.renegotiate(lb, sch, ds.today, market["rate_pct"], country, None, fees.get("bank_fees", 0), fees.get("guarantee_fees", 0),
                                  fees.get("other_fees", 0), fees.get("penalty"))
        except ValueError as e:
            res = {"status": "invalid", "note": str(e)}
        out["renegotiation"] = res
        if res.get("status") == "computed" and not any(fees.values() if fees else []):
            out["renegotiation_note"] = ("no fee was given: add the bank fees, the guarantee fees and the penalty of your contract to price it "
                                         "(`coach rental indicators --bank-fees ...`)")
    return out


def indicators(ds, prop, *, market_rate_pct: Optional[float] = None, market_rate_date: Optional[dt.date] = None,
               fees: Optional[dict] = None) -> dict:
    today = ds.today
    market = market_of(prop, market_rate_pct, market_rate_date, today)
    rate = rate_check(ds, prop, market, fees)
    eq = equity(ds, prop)
    sch = SC.status(ds, prop, ds.settings.rental_reminder_months)
    rows, _rent = CF.all_rows(ds, prop)
    done = [r for r in rows if r.complete][-12:]
    effort = div_cents(sum(r.effort_c for r in done), len(done)) if done else None
    signals: list = []
    if rate.get("status") == "above_market":
        signals.append({"id": "rate_above_market", "reading": rate["reading"]})
    if sch.get("state") == "active" and sch.get("months_left") is not None and sch["months_left"] <= ds.settings.rental_reminder_months:
        signals.append({"id": "commitment_ending", "reading": f"the commitment ends in about {sch['months_left']} month(s) ({sch['end_date']}): "
                        "extend (if the scheme allows it), keep renting under the ordinary rules, or sell. Check the consequences of each with "
                        f"the scheme's rules or an adviser. {GENERAL}"})
    elif sch.get("state") == "active":
        signals.append({"id": "commitment_running", "reading": "the commitment is running: ending it early (selling, stopping the rental) can call "
                        "the scheme's advantage into question. Verify with the scheme's rules before any decision."})
    elif sch.get("state") == "ended":
        signals.append({"id": "commitment_over", "reading": f"the commitment ended on {sch.get('effective_end_date')}: the scheme's constraints "
                        "(rent cap, tenant limits) may no longer apply; verify what changes for the property."})
    if eq["status"] == "computed":
        signals.append({"id": "equity_positive" if eq["net_equity_c"] >= 0 else "equity_negative",
                        "reading": "the declared valuation is above the capital still due" if eq["net_equity_c"] >= 0
                        else "the declared valuation is below the capital still due: selling would not repay the loan"})
    out = {"as_of": today, "market_rate": market, "loan_rate": rate, "commitment": {k: sch.get(k) for k in (
        "state", "start_date", "years", "end_date", "effective_end_date", "days_left", "months_left", "decision_needed", "extension")},
           "equity": eq, "trailing_effort_c": effort, "signals": signals,
           "scenarios": ["`coach loans scenario renegotiate <loan> --new-rate R` (or the `mortgage_check` tool with `market_rate_pct`): the "
                         "renegotiation priced on the real schedule", "`coach loans scenario prepay <loan> --amount N` or the `what_if` tool "
                         "(`prepay_loan`): a prepayment on the exact schedule"],
           "disclaimer": D.LOAN["en"]}
    return out
