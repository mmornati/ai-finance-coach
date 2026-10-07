"""The yearly amount of a scheme's tax reduction (E15-4): ONE function for the rental page / tool and for ``tax_candidates``.

Which yearly shape is used, in this order (the result says which, in ``basis``):

    declared_schedule  the owner typed ``commitment.reduction_schedule`` (segments of ``years`` at ``yearly_rate_pct`` of the eligible price);
    pinel_table        the scheme is a Pinel: the known split. A 6 or 9 year commitment is spread evenly (2 % a year for the classic rates); a 12 year one is
                       the 9-year rate over the first 9 years, then the extra rate over the last 3 (classic: 18 % over 9 years = 2 % a year, then 3 % over 3 years =
                       1 % a year, 21 % in all). The rates depend on the year of the purchase (up to 2022 classic, 2023 and 2024 reduced). LAST_REVIEWED says when
                       the table was written: verify it. A total rate the owner declared (``reduction_rate_pct``) that differs from the table keeps the 9 + 3 shape
                       in the table's proportion;
    even_spread        anything else: ``reduction_rate_pct`` spread evenly over the years, flagged as an ASSUMPTION.

The eligible price is the purchase price, capped by the owner's ``reduction_base_cap`` and, for a Pinel, by the 300,000 EUR and 5,500 EUR/m2 ceilings (the
latter only when ``surface_m2`` is declared), whichever is lowest.
"""
from __future__ import annotations

from typing import Optional

from coach.analytics.common import div_cents, money_str, mul_cents, to_cents
from coach.i18n_msg import server_msg

LAST_REVIEWED = "2026-10"
PINEL_PRICE_CAP_C = 30_000_000       # EUR 300,000 per property
PINEL_M2_CAP_C = 550_000             # EUR 5,500 per m2 of habitable surface
# purchase-year bucket -> commitment years -> [(years, total % of the price over those years)]
PINEL_TABLE = {
    "classic": {6: [(6, 12.0)], 9: [(9, 18.0)], 12: [(9, 18.0), (3, 3.0)]},
    2023: {6: [(6, 10.5)], 9: [(9, 15.0)], 12: [(9, 15.0), (3, 2.5)]},
    2024: {6: [(6, 9.0)], 9: [(9, 12.0)], 12: [(9, 12.0), (3, 2.0)]},
}
VERIFY = (f"the Pinel split (rates by purchase year, 9 + 3 years for a 12 year commitment, 300,000 EUR and 5,500 EUR/m2 ceilings) is the one known in "
          f"{LAST_REVIEWED}: verify it on impots.gouv.fr")
# Every note below is a message of the web (i18n step 4, docs/i18n.md "Server text"): ``notes`` keeps the English (its ``text``) for the CLI,
# the tools and ``tax_candidates``; ``notes_msg`` (same order) is what the web translates.
VERIFY_MSG = server_msg("rental.reduction.verify", VERIFY, reviewed_month=LAST_REVIEWED, price_cap_amount=money_str(PINEL_PRICE_CAP_C),
                        m2_cap_amount=money_str(PINEL_M2_CAP_C))


def is_pinel(asset) -> bool:
    return str(getattr(asset, "scheme", "") or "").lower().startswith("pinel")


def years_of(asset) -> Optional[int]:
    com = getattr(asset, "commitment", None)
    return (com.years if com and com.years else None) or getattr(asset, "pinel_commitment_years", None) or None


def first_year_of(asset) -> Optional[int]:
    com = getattr(asset, "commitment", None)
    if com and com.reduction_first_year:
        return com.reduction_first_year
    if getattr(asset, "purchase_date", None):
        return asset.purchase_date.year
    return com.start_date.year if com and com.start_date else None


def _table(asset, years: int, purchase_year: Optional[int]):
    bucket = purchase_year if purchase_year in (2023, 2024) else "classic"
    return PINEL_TABLE[bucket].get(years), bucket


def _segments(asset, years: int, purchase_year: Optional[int]) -> tuple[Optional[list], str, list]:
    """([(years, total %)], basis, notes): the notes are messages (``server_msg``)."""
    com = getattr(asset, "commitment", None)
    notes: list = []
    sched = getattr(com, "reduction_schedule", None) if com else None
    if sched:
        segs = [(s.years, round(s.years * s.yearly_rate_pct, 6)) for s in sched]
        return segs, "declared_schedule", [server_msg("rental.reduction.declaredSchedule",
                                                      "the yearly schedule is the one you declared (commitment.reduction_schedule)")]
    declared = com.reduction_rate_pct if com else None
    if is_pinel(asset):
        tbl, bucket = _table(asset, years, purchase_year)
        if tbl:
            if declared is not None and abs(declared - sum(p for _y, p in tbl)) > 1e-9:
                if len(tbl) == 2:
                    k = declared / sum(p for _y, p in tbl)
                    tbl = [(y, p * k) for y, p in tbl]
                    notes.append(server_msg("rental.reduction.shapeKept",
                                            "your declared total rate differs from the Pinel table: its 9 + 3 year shape is kept in the table's proportion"))
                else:
                    tbl = [(years, declared)]
                    notes.append(server_msg("rental.reduction.spreadEvenly",
                                            "your declared total rate differs from the Pinel table: it is spread evenly over the commitment"))
                return tbl, "pinel_table", notes + [VERIFY_MSG]
            rates = (server_msg("rental.reduction.ratesClassic", "Pinel rates of a purchase up to 2022") if bucket == "classic"
                     else server_msg("rental.reduction.ratesOfYear", f"Pinel rates of a {bucket} purchase", purchase_year=bucket))
            return tbl, "pinel_table", [rates, VERIFY_MSG]
    if declared is None:
        return None, "none", []
    return [(years, declared)], "even_spread", [server_msg(
        "rental.reduction.assumption",
        "ASSUMPTION: the total rate is spread evenly over the commitment years; declare `commitment.reduction_schedule` (segments of years at a yearly rate) "
        "if the scheme spreads it differently")]


def eligible_base_c(asset) -> tuple[int, int, bool, list]:
    """(base, price, capped, notes) in cents; the notes are messages (``server_msg``)."""
    com = getattr(asset, "commitment", None)
    price_c = to_cents(asset.purchase_price)
    base, notes = price_c, []
    caps = []
    if com and com.reduction_base_cap is not None:
        c = to_cents(com.reduction_base_cap)
        caps.append((c, server_msg("rental.reduction.cappedDeclared", "the price is capped by the ceiling you declared", cap_amount=money_str(c))))
    if is_pinel(asset):
        caps.append((PINEL_PRICE_CAP_C, server_msg("rental.reduction.cappedPinel", "the price is capped by the Pinel ceiling of 300,000 EUR",
                                                   cap_amount=money_str(PINEL_PRICE_CAP_C))))
        if com and com.surface_m2:
            c = mul_cents(PINEL_M2_CAP_C, com.surface_m2)
            caps.append((c, server_msg("rental.reduction.cappedM2", f"the price is capped by 5,500 EUR/m2 x {com.surface_m2} m2",
                                       m2_cap_amount=money_str(PINEL_M2_CAP_C), surface=com.surface_m2, cap_amount=money_str(c))))
    for c, msg in caps:
        if c < base:
            base = c
            notes.append(msg)
    return base, price_c, base < price_c, notes


def compute(asset, year: int) -> dict:
    """The reduction of `year` for a property asset. ``status``: computed | needs_fields."""
    com = getattr(asset, "commitment", None)
    years = years_of(asset)
    first = first_year_of(asset)
    py = asset.purchase_date.year if getattr(asset, "purchase_date", None) else None
    need = []
    if getattr(asset, "purchase_price", None) is None:
        need.append("purchase_price")
    if not years:
        need.append("commitment.years")
    if first is None:
        need.append("commitment.reduction_first_year (or purchase_date / commitment.start_date)")
    if need:
        return {"status": "needs_fields", "missing": need, "missing_msg": [
            server_msg("rental.reduction.missingFirstYear", m, field="commitment.reduction_first_year") if m.startswith("commitment.reduction_first_year")
            else None for m in need]}
    segs, basis, notes = _segments(asset, years, py)
    if segs is None:
        m = "commitment.reduction_rate_pct (the total reduction over the commitment, from the scheme's table)"
        return {"status": "needs_fields", "missing": [m], "missing_msg": [server_msg("rental.reduction.missingRate", m, field="commitment.reduction_rate_pct")]}
    base_c, price_c, capped, cap_notes = eligible_base_c(asset)
    schedule, y = [], first
    for n, total_pct in segs:
        seg_total = mul_cents(base_c, total_pct / 100)
        for _ in range(n):
            schedule.append({"year": y, "amount_c": div_cents(seg_total, n)})
            y += 1
    out = {"status": "computed", "basis": basis, "base_c": base_c, "price_c": price_c, "base_capped": capped, "years": years, "first_year": first,
           "last_year": y - 1, "year": year, "total_c": sum(s["amount_c"] for s in schedule), "annual_c": schedule[0]["amount_c"],
           "rate_pct": round(sum(p for _n, p in segs), 6), "schedule": schedule, "last_reviewed": LAST_REVIEWED if basis == "pinel_table" else None}
    ext = com.extension if com else None
    if ext and ext.decision == "extend" and ext.years and ext.additional_rate_pct is not None:
        ext_total = mul_cents(base_c, ext.additional_rate_pct / 100)
        per = div_cents(ext_total, ext.years)
        out["extension"] = {"years": ext.years, "additional_rate_pct": ext.additional_rate_pct, "total_c": ext_total, "annual_c": per,
                            "first_year": y, "last_year": y + ext.years - 1}
        schedule += [{"year": y + i, "amount_c": per, "extension": True} for i in range(ext.years)]
    hit = next((s for s in schedule if s["year"] == year), None)
    out["candidate_c"] = hit["amount_c"] if hit else 0
    out["in_window"] = hit is not None
    if hit and hit.get("extension"):
        out["via_extension"] = True
    out["years_elapsed_including_this"] = max(0, min(year - first + 1, years))
    msgs = cap_notes + notes + [
        server_msg("rental.reduction.notADeduction", "the reduction lowers the tax, it is not a deduction from the rental income, and it does not "
                   "depend on the micro-foncier / reel choice"),
        server_msg("rental.reduction.nothingLookedUp", "the rate, the price and the ceilings are the ones recorded on the property (declared by you, "
                   "or the dated Pinel table): nothing is looked up")]
    out["notes"] = [m["text"] for m in msgs]
    out["notes_msg"] = msgs
    return out
