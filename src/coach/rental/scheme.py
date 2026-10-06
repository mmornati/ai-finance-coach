"""The commitment of a tax-incentive scheme (E15-3): dates, reminders, the decision at the end, and the checks of the user's own figures.

A "scheme" is generic (Pinel is only the best known): its name is data (``scheme``), its length is ``commitment.years`` (6, 9 or 12 for a
Pinel-type scheme, any whole number is accepted), and the rent cap, the tenant income limit and the reduction rate are WHATEVER THE OWNER
TYPED from the deed or the scheme's table: nothing is looked up and a missing figure is listed (and becomes an open question), never guessed.

End date = the day before the anniversary of the start (start + years) unless ``commitment.end_date`` is given; it is the usual way a
commitment is counted, to verify with the deed. An ``extension`` decision (extend N years / not extend) is recorded in memory by the owner;
while it is ``undecided`` the reminders run (``[analytics] rental_reminder_months`` before the end, then at six and three months), and they
stop once a decision is recorded.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from coach.analytics.common import add_months, to_cents

COMMON_YEARS = (6, 9, 12)
REMINDER_STEPS = (12, 6, 3)        # months before the end where a reminder is due (the first one is `rental_reminder_months`)


def declared(prop) -> bool:
    a = prop.asset
    return bool(getattr(a, "scheme", None) or getattr(a, "commitment", None) or getattr(a, "pinel_commitment_years", None))


def years_of(prop) -> Optional[int]:
    com = prop.commitment
    return (com.years if com and com.years else None) or getattr(prop.asset, "pinel_commitment_years", None) or None


def end_of(start: dt.date, years: int) -> dt.date:
    return add_months(start, 12 * years) - dt.timedelta(days=1)


def dates(prop) -> dict:
    """start, years, the end of the initial commitment and the effective end (extension included)."""
    com = prop.commitment
    start = com.start_date if com else None
    years = years_of(prop)
    end = com.end_date if com and com.end_date else (end_of(start, years) if start and years else None)
    ext = com.extension if com else None
    eff = end
    if end and ext and ext.decision == "extend" and ext.years:
        eff = end_of(end + dt.timedelta(days=1), ext.years)
    return {"start": start, "years": years, "end": end, "end_source": "declared" if com and com.end_date else "start + years",
            "effective_end": eff, "extension": ext}


def whole_months(a: dt.date, b: dt.date) -> int:
    """Whole months from a to b (b >= a), by calendar."""
    m = (b.year - a.year) * 12 + b.month - a.month
    return m - (1 if b.day < a.day else 0)


def rent_cap_c(prop) -> tuple[Optional[int], Optional[str]]:
    com = prop.commitment
    if com is None:
        return None, None
    if com.rent_cap_monthly is not None:
        return to_cents(com.rent_cap_monthly), "the monthly cap you declared"
    if com.rent_cap_m2 is not None and com.surface_m2:
        return to_cents(com.rent_cap_m2 * com.surface_m2), "the cap per m2 you declared x the surface you declared"
    return None, None


def missing_facts(prop) -> list[dict]:
    """What the owner has not recorded yet, each with what it is needed for. Scheme facts are asked only for a property under a scheme."""
    a, com = prop.asset, prop.commitment
    out: list[dict] = []

    def need(field: str, why: str, ok: bool) -> None:
        if not ok:
            out.append({"field": field, "needed_for": why})
    need("account", "the property's bank account (its flows, the P&L)", prop.account_link in ("declared", "only_one"))
    need("loan", "the loan that financed it (interest, outstanding capital, equity)", bool(prop.loans))
    need("value", "valuation for the net equity (the latest value you believe, with as_of)", getattr(a, "value", None) is not None)
    need("rent_monthly", "the expected rent (vacancy and rent cap checks)", bool(getattr(a, "rent_monthly", None)))
    need("purchase_price", "the figures of the tax return and the scheme reduction", getattr(a, "purchase_price", None) is not None)
    need("purchase_date", "the figures of the tax return and the scheme reduction", getattr(a, "purchase_date", None) is not None)
    if declared(prop):
        need("commitment.start_date", "the end date of the commitment and its reminders", bool(com and com.start_date))
        need("commitment.years", "the end date of the commitment (6, 9 or 12 for a Pinel-type scheme)", years_of(prop) is not None)
        cap, _ = rent_cap_c(prop)
        need("commitment.rent_cap_monthly (or rent_cap_m2 and surface_m2)", "the rent cap check", cap is not None)
        need("commitment.tenant_income_limit", "the tenant income check", bool(com and com.tenant_income_limit is not None))
        need("commitment.reduction_rate_pct", "the scheme reduction candidate of the tax return", bool(com and com.reduction_rate_pct is not None))
    return out


def status(ds, prop, reminder_months: int = 12, observed_rent_c: Optional[int] = None) -> dict:
    """Everything about the commitment of a property. ``observed_rent_c``: the last rent received (used only when no rent is declared)."""
    today = ds.today
    d = dates(prop)
    a = prop.asset
    out: dict = {"declared": declared(prop), "scheme": getattr(a, "scheme", None), "start_date": d["start"], "years": d["years"],
                 "end_date": d["end"], "end_source": d["end_source"] if d["end"] else None, "effective_end_date": d["effective_end"],
                 "state": "unknown", "missing": missing_facts(prop)}
    if d["years"] and d["years"] not in COMMON_YEARS and str(getattr(a, "scheme", "") or "").lower().startswith("pinel"):
        out["warnings"] = [f"a Pinel-type commitment is usually 6, 9 or 12 years: {d['years']} was recorded, check the deed"]
    ext = d["extension"]
    out["extension"] = ({"decision": ext.decision, "years": ext.years, "additional_rate_pct": ext.additional_rate_pct,
                         "decided_on": ext.decided_on, "note_recorded": bool(ext.note)} if ext else {"decision": "undecided"})
    end = d["end"]
    if d["start"] and end:
        total = max((end - d["start"]).days, 1)
        if today < d["start"]:
            out["state"] = "not_started"
        elif today <= (d["effective_end"] or end):
            out["state"] = "active"
        else:
            out["state"] = "ended"
        out["progress_pct"] = round(min(max((today - d["start"]).days / total, 0), 1) * 100, 1)
        out["days_left"] = (end - today).days
        out["months_left"] = whole_months(today, end) if end >= today else 0
        rem = []
        for m in sorted({reminder_months, *REMINDER_STEPS}, reverse=True):
            if m <= reminder_months:
                rem.append({"months_before": m, "date": add_months(end, -m)})
        out["reminders"] = rem
        due = [r for r in rem if r["date"] <= today]
        undecided = out["extension"]["decision"] == "undecided"
        out["decision_needed"] = bool(undecided and due and today <= add_months(end, 6))
        out["next_reminder_date"] = next((r["date"] for r in sorted(rem, key=lambda r: r["date"]) if r["date"] > today), None)
    cap, cap_src = rent_cap_c(prop)
    rent_decl = to_cents(a.rent_monthly) if getattr(a, "rent_monthly", None) else None
    rent_c, rent_src = (rent_decl, "the lease rent you declared") if rent_decl else (
        (observed_rent_c, "the last rent received (it may be net of management fees: the cap applies to the lease rent)")
        if observed_rent_c else (None, None))
    rc: dict = {"cap_c": cap, "cap_basis": cap_src, "rent_c": rent_c, "rent_basis": rent_src, "status": "unknown"}
    if cap is not None and rent_c is not None:
        rc["gap_c"] = rent_c - cap
        rc["status"] = "above_cap" if rent_c > cap else "within_cap"
    out["rent_cap"] = rc
    com = prop.commitment
    tl: dict = {"limit_c": to_cents(com.tenant_income_limit) if com and com.tenant_income_limit is not None else None,
                "tenant_income_c": to_cents(com.tenant_income) if com and com.tenant_income is not None else None, "status": "unknown"}
    if tl["limit_c"] is not None and tl["tenant_income_c"] is not None:
        tl["headroom_c"] = tl["limit_c"] - tl["tenant_income_c"]
        tl["status"] = "above_limit" if tl["tenant_income_c"] > tl["limit_c"] else "within_limit"
    out["tenant_income"] = tl
    return out
