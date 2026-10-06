"""E15-4: the yearly shape of a scheme reduction, ONE shared function for the rental page / tool and `tax_candidates`. Hand-computed: Pinel 12 years at the
classic 21 % on 250,000 EUR is 5,000 a year for 9 years then 2,500 a year for 3."""
from __future__ import annotations

import pytest

from anhelpers import D, account, make_ds
from coach.analytics.dataset import MemorySnapshot
from coach.memory import schemas as S
from coach.rental import model as M, reduction as RED, taxyear as TX
from coach.skills import tax as SKT


def pinel(py=2020, years=12, price=250000, rate=None, **com):
    commitment = {"start_date": f"{py}-07-01", "years": years, "reduction_first_year": py, **com}
    if rate is not None:
        commitment["reduction_rate_pct"] = rate
    return S.Asset(id="flat-1", kind="real_estate_rental", scheme="pinel", purchase_price=price, purchase_date=f"{py}-06-01", commitment=commitment)


def amounts(asset, first, last):
    return [RED.compute(asset, y)["candidate_c"] for y in range(first, last + 1)]


def test_a_twelve_year_pinel_is_nine_years_at_the_high_rate_then_three_at_the_low_one():
    a = pinel(2020, 12)
    assert amounts(a, 2020, 2031) == [500000] * 9 + [250000] * 3
    r = RED.compute(a, 2030)
    assert r["basis"] == "pinel_table" and r["total_c"] == 5250000 and r["rate_pct"] == 21.0 and r["last_year"] == 2031 and r["last_reviewed"] == "2026-10"
    assert any("verify" in n for n in r["notes"]) and not RED.compute(a, 2032)["in_window"] and RED.compute(a, 2019)["candidate_c"] == 0


def test_six_and_nine_years_are_even_at_two_percent_a_year():
    assert amounts(pinel(2020, 6), 2020, 2026) == [500000] * 6 + [0]
    assert amounts(pinel(2020, 9), 2020, 2029) == [500000] * 9 + [0]
    assert RED.compute(pinel(2020, 6), 2020)["total_c"] == 3000000 and RED.compute(pinel(2020, 9), 2020)["total_c"] == 4500000


@pytest.mark.parametrize("py,years,expected", [
    (2023, 6, [437500] * 6), (2023, 9, [416667] * 9), (2023, 12, [416667] * 9 + [208333] * 3),
    (2024, 6, [375000] * 6), (2024, 9, [333333] * 9), (2024, 12, [333333] * 9 + [166667] * 3)])
def test_the_reduced_rates_of_2023_and_2024_keep_the_nine_plus_three_pattern(py, years, expected):
    assert amounts(pinel(py, years), py, py + years - 1) == expected


def test_a_declared_total_rate_that_differs_keeps_the_pinel_shape_in_the_table_proportion():
    r = amounts(pinel(2020, 12, rate=10.5), 2020, 2031)             # half of 21 %: 2,500 x 9 then 1,250 x 3
    assert r == [250000] * 9 + [125000] * 3
    assert "table's proportion" in " ".join(RED.compute(pinel(2020, 12, rate=10.5), 2020)["notes"])


def test_a_declared_schedule_wins_over_everything():
    a = pinel(2020, 6, rate=99, reduction_schedule=[{"years": 4, "yearly_rate_pct": 3}, {"years": 2, "yearly_rate_pct": 1}])
    assert amounts(a, 2020, 2025) == [750000] * 4 + [250000] * 2
    r = RED.compute(a, 2020)
    assert r["basis"] == "declared_schedule" and r["total_c"] == 3500000 and r["last_year"] == 2025
    for bad in ({"years": 0, "yearly_rate_pct": 1}, {"years": 3, "yearly_rate_pct": 120}):
        with pytest.raises(Exception):
            S.ReductionSegment(**bad)


def test_another_scheme_with_a_declared_rate_is_spread_evenly_and_flagged_as_an_assumption():
    a = S.Asset(id="x", kind="real_estate_rental", scheme="my-scheme", purchase_price=100000, purchase_date="2020-01-01",
                commitment={"start_date": "2020-02-01", "years": 10, "reduction_rate_pct": 20})
    r = RED.compute(a, 2024)
    assert r["basis"] == "even_spread" and r["candidate_c"] == 200000 and any("ASSUMPTION" in n for n in r["notes"])
    assert RED.compute(S.Asset(id="x", kind="real_estate_rental", scheme="my-scheme", purchase_price=1, purchase_date="2020-01-01",
                               commitment={"years": 5}), 2024)["status"] == "needs_fields"


def test_the_price_is_capped_by_300000_and_5500_per_m2_whichever_is_lower():
    assert RED.compute(pinel(2020, 9, price=350000), 2020)["base_c"] == 30000000
    r = RED.compute(pinel(2020, 9, price=250000, surface_m2=40), 2020)             # 5,500 x 40 = 220,000
    assert r["base_c"] == 22000000 and r["base_capped"] and r["candidate_c"] == 440000 and any("5,500 EUR/m2" in n for n in r["notes"])
    assert RED.compute(pinel(2020, 9, price=250000, surface_m2=80), 2020)["base_c"] == 25000000          # 440,000 per m2 cap is above the price: no cap
    assert RED.compute(pinel(2020, 9, price=250000, reduction_base_cap=200000, surface_m2=80), 2020)["base_c"] == 20000000
    other = S.Asset(id="x", kind="real_estate_rental", scheme="other", purchase_price=350000, purchase_date="2020-01-01",
                    commitment={"years": 9, "reduction_rate_pct": 10, "surface_m2": 10, "reduction_first_year": 2020})
    assert RED.compute(other, 2020)["base_c"] == 35000000                              # the Pinel ceilings are not applied to another scheme


def test_an_extension_adds_its_own_years_after_the_schedule():
    a = pinel(2020, 12, extension={"decision": "extend", "years": 3, "additional_rate_pct": 3})
    r = RED.compute(a, 2033)
    assert r["candidate_c"] == 250000 and r["via_extension"] is True and r["extension"]["last_year"] == 2034
    assert RED.compute(a, 2035)["candidate_c"] == 0


def test_tax_candidates_and_the_rental_page_give_the_same_figure_every_year():
    for py, years in ((2020, 12), (2023, 12), (2024, 9)):
        asset = pinel(py, years)
        ds = make_ds([], [account("rent", "Rental", purpose="rental")], today=D(f"{py + years + 1}-03-01"),
                     memory=MemorySnapshot(assets=[asset], country="FR"))
        (p,) = M.properties(ds)
        for y in range(py - 1, py + years + 1):
            page = TX.scheme_reduction(p, y)["candidate_c"]
            cands = {c["id"]: c for c in SKT.tax_candidates(ds, y, "FR")["candidates"]}
            shown = cands["fr-pinel"]["estimated_benefit"]
            expected = f"{page // 100}.{page % 100:02d}"
            if page:
                assert shown["high"] == expected or (py in (2023, 2024) and shown["low"] == expected), (py, years, y, shown, expected)
            else:
                assert shown == {"low": None, "high": None}
