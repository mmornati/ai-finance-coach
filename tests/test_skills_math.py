"""E7-6 / E7-7 / E7-8: the deterministic helpers of the skills (loans, savings, cancellability) against hand-computed values.

Reference values are textbook annuity tables (200,000 EUR at 6 % over 360 months = 1,199.10; 150,000 at 4 % / 3 % over 240 months =
908.97 / 831.90; 100,000 at 12 % over 12 months = 8,884.88) or computed by hand in the comments. No database, no network."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from coach.skills import cancel as C, loans as L
from coach.skills.savings import savings_estimate

D = dt.date
from pathlib import Path


# ---------------------------------------------------------------- annuities and schedules

def test_annuity_matches_textbook_tables():
    assert L.annuity_c(20_000_000, 6, 360) == 119_910
    assert L.annuity_c(15_000_000, 4, 240) == 90_897
    assert L.annuity_c(15_000_000, 3, 240) == 83_190
    assert L.annuity_c(10_000_000, 12, 12) == 888_488
    assert L.annuity_c(120_000, 0, 12) == 10_000                   # a zero-rate loan is capital / months
    with pytest.raises(ValueError):
        L.annuity_c(0, 3, 12)


def test_schedule_of_a_twelve_month_loan_is_exact_and_ends_at_zero():
    rows = L.schedule(10_000_000, 12, 12)
    # month 1: interest = 100,000.00 x 1 % = 1,000.00; principal = 8,884.88 - 1,000.00 = 7,884.88; balance 92,115.12
    assert (rows[0].interest_c, rows[0].principal_c, rows[0].payment_c, rows[0].balance_c) == (100_000, 788_488, 888_488, 9_211_512)
    # month 2: interest = 92,115.12 x 1 % = 921.15 (921.1512 rounded)
    assert rows[1].interest_c == 92_115
    assert rows[-1].balance_c == 0 and sum(r.principal_c for r in rows) == 10_000_000
    assert abs(L.total_interest_c(rows) - 661_856) <= 5          # 12 x 8,884.88 - 100,000 = 6,618.56 (the last instalment absorbs the rounding)


def test_schedule_with_fixed_payment_shortens_the_term():
    # 120,000 at 0 %, 1,000 a month: 120 months; after a 24,000 prepayment 96 months, no interest either way
    assert len(L.schedule_fixed_payment(12_000_000, 0, 100_000)) == 120
    assert len(L.schedule_fixed_payment(9_600_000, 0, 100_000)) == 96
    with pytest.raises(ValueError):
        L.schedule_fixed_payment(10_000_000, 12, 5_000)            # does not even cover the interest


def test_implied_rate_round_trips_and_refuses_the_impossible():
    pay = L.annuity_c(15_000_000, 2.49, 220)
    assert L.implied_rate_pct(15_000_000, pay, 220) == Decimal("2.49")
    assert L.implied_rate_pct(15_000_000, 1000, 100) is None       # 100 x 10.00 < the capital
    assert L.implied_rate_pct(0, 1000, 100) is None


# ---------------------------------------------------------------- IRA, renegotiation, surroga, insurance

def test_french_ira_is_the_lower_of_six_months_of_interest_and_three_percent():
    # 150,000 at 4 %: six months of interest = 150,000 x 4 % / 2 = 3,000.00 ; 3 % = 4,500.00  -> 3,000.00
    assert L.ira_penalty_c(15_000_000, 4)[0] == 300_000
    # at 8 %: six months = 6000.00 > 4,500.00 -> the 3 % cap
    pen, why = L.ira_penalty_c(15_000_000, 8)
    assert pen == 450_000 and "3 %" in why
    assert L.ira_penalty_c(15_000_000, 6)[0] == 450_000            # equal: 4,500.00 both ways


def test_rachat_estimate_hand_computed():
    r = L.renegotiation_estimate(current_rate_pct=4, new_rate_pct=3, remaining_capital=150_000, remaining_months=240,
                                 bank_fees=1_000, guarantee_fees=1_500)
    assert (r.variant, r.country) == ("rachat", "FR")
    assert r.current_payment_c == 90_897 and r.new_payment_c == 83_190 and r.monthly_saving_c == 7_707
    assert abs(r.interest_current_c - (90_897 * 240 - 15_000_000)) <= 200         # 68,152.80 (the last instalment absorbs the rounding)
    assert abs(r.interest_new_c - (83_190 * 240 - 15_000_000)) <= 200             # 49,656.00
    assert r.penalty_c == 300_000 and r.total_costs_c == 300_000 + 100_000 + 150_000
    assert r.net_saving_c == r.gross_interest_saving_c - 550_000
    assert r.break_even_months == 72                                               # ceil(5,500 / 77.07) = 72
    assert r.rule_of_thumb_met and r.verdict == "worth_asking_for_quotes" and "bank" in r.disclaimer


def test_renegotiation_with_the_same_bank_has_no_ira_and_surroga_has_no_penalty_at_all():
    same = L.renegotiation_estimate(current_rate_pct=4, new_rate_pct=3, remaining_capital=150_000, remaining_months=240,
                                    variant="renegotiation", bank_fees=500)
    assert same.penalty_c == 0 and same.total_costs_c == 50_000 and same.break_even_months == 7        # ceil(500 / 77.07)
    sur = L.renegotiation_estimate(current_rate_pct=4, new_rate_pct=3, remaining_capital=150_000, remaining_months=240, country="IT")
    assert sur.variant == "surroga" and sur.penalty_c == 0 and sur.total_costs_c == 0 and sur.break_even_months == 0
    assert "Bersani" in sur.penalty_basis and any("surroga" in n for n in sur.notes)
    with pytest.raises(ValueError):
        L.renegotiation_estimate(current_rate_pct=4, new_rate_pct=3, remaining_capital=1000, remaining_months=12, country="FR", variant="surroga")


def test_a_higher_market_rate_is_no_saving_and_a_contract_penalty_overrides_the_cap():
    r = L.renegotiation_estimate(current_rate_pct=2, new_rate_pct=3.5, remaining_capital=100_000, remaining_months=180)
    assert r.verdict == "no_saving" and r.monthly_saving_c < 0 and r.break_even_months is None
    o = L.renegotiation_estimate(current_rate_pct=4, new_rate_pct=3, remaining_capital=150_000, remaining_months=240, penalty_override=1200)
    assert o.penalty_c == 120_000 and "contract" in o.penalty_basis
    small = L.renegotiation_estimate(current_rate_pct=3.0, new_rate_pct=2.9, remaining_capital=40_000, remaining_months=60,
                                     bank_fees=2000)
    assert small.verdict == "costs_exceed_saving" and not small.rule_of_thumb_met


def test_insurance_delegation_hand_computed():
    d = L.insurance_delegation_estimate(current_monthly=60, alternative_monthly=25, remaining_months=200, fees=0)
    # (60 - 25) x 200 = 7,000.00 ; 35.00 a month ; 420.00 a year
    assert (d.monthly_saving_c, d.yearly_saving_c, d.total_saving_c, d.net_saving_c) == (3500, 42000, 700000, 700000)
    assert d.verdict == "saving" and any("Lemoine" in n for n in d.notes)
    worse = L.insurance_delegation_estimate(current_monthly=20, alternative_monthly=25, remaining_months=100)
    assert worse.verdict == "no_saving"
    it = L.insurance_delegation_estimate(current_monthly=60, alternative_monthly=25, remaining_months=10, country="IT", fees=400)
    assert it.verdict == "fees_exceed_saving" and any("IVASS" in n for n in it.notes)


def test_prepayment_exact_and_approximate():
    # zero-rate loan, 1,000 a month, 120,000 left: prepaying 24,000 keeping the payment saves 24 months and 0 interest
    p = L.prepayment_effect(capital_c=12_000_000, amount_c=2_400_000, rate_pct=0, payment_c=100_000, remaining_months=120, keep="payment")
    assert (p.months_saved, p.interest_saved_c, p.monthly_effect_c, p.method, p.approximate) == (24, 0, 0, "amortization", False)
    # keeping the term instead: 96,000 over 120 months = 800.00 a month: the instalment falls by 200.00
    t = L.prepayment_effect(capital_c=12_000_000, amount_c=2_400_000, rate_pct=0, payment_c=100_000, remaining_months=120, keep="term")
    assert t.payment_after_c == 80_000 and t.monthly_effect_c == -20_000 and t.months_saved == 0
    # with a rate the interest saving is positive and the penalty is the IRA of the amount prepaid (six months of interest)
    r = L.prepayment_effect(capital_c=15_000_000, amount_c=2_000_000, rate_pct=3, payment_c=None, remaining_months=240, keep="payment",
                            ira_possible=True)
    assert r.months_saved > 0 and r.interest_saved_c > 0 and r.penalty_estimate_c == 30_000        # 20,000 x 3 % / 2 = 300.00
    # unknown rate: a flagged zero-interest approximation
    a = L.prepayment_effect(capital_c=15_000_000, amount_c=2_000_000, rate_pct=None, payment_c=100_000, remaining_months=None, keep="payment")
    assert a.approximate and a.months_saved == 20 and a.interest_saved_c is None and a.method == "approximation"
    with pytest.raises(ValueError):
        L.prepayment_effect(capital_c=1000, amount_c=1000, rate_pct=2, payment_c=10, remaining_months=10)


# ---------------------------------------------------------------- loan state from the facts on file

def facts(**kw) -> L.LoanFacts:
    base = dict(id="home", kind="mortgage", principal_c=25_000_000, rate_pct=Decimal("2.1"), start=D(2020, 1, 1), end=D(2040, 1, 1),
                payment_c=127_658, outstanding_c=18_000_000, outstanding_as_of=D(2026, 9, 20))
    base.update(kw)
    return L.LoanFacts(**base)


def test_loan_state_computes_the_schedule_and_prefers_a_recent_declared_capital():
    st = L.loan_state(facts(), D(2026, 10, 5))
    am = st["amortization"]
    assert st["missing_for_amortization"] == [] and am["term_months"] == 240
    # float reference: P r / (1 - (1+r)^-n)
    r = 0.021 / 12
    ref = 250_000 * r / (1 - (1 + r) ** -240)
    assert abs(float(am["payment_theoretical"]) - ref) < 0.01
    assert am["payments_made"] == 81 and am["remaining_instalments"] == 159          # instalments 1..81 are due 2020-02-01 .. 2026-10-01
    assert am["payment_check"]["status"] == "matches"
    assert st["remaining_capital_source"] == "declared (recent)" and st["_remaining_capital_c"] == 18_000_000
    assert st["remaining_months_to_end_date"] == 159
    assert sum(float(y["principal"]) for y in am["by_year"]) == pytest.approx(250_000, abs=0.5)
    assert {y["year"] for y in am["by_year"]} == set(range(2020, 2041))


def test_an_old_declared_capital_is_replaced_by_the_computed_one_and_a_wrong_instalment_is_said():
    st = L.loan_state(facts(outstanding_as_of=D(2026, 1, 15), payment_c=150_000), D(2026, 10, 5))
    assert st["remaining_capital_source"] == "computed from the schedule"
    assert st["amortization"]["payment_check"]["status"] == "differs"
    inc = L.loan_state(facts(payment_c=127_658 + 5_000, insurance_monthly_c=5_000), D(2026, 10, 5))
    assert inc["amortization"]["payment_check"]["status"] == "matches_when_insurance_is_included"
    far = L.loan_state(facts(outstanding_c=10_000_000, outstanding_as_of=D(2026, 10, 1)), D(2026, 10, 5))
    assert far["outstanding_check"]["status"] == "differs"


def test_missing_fields_are_listed_and_never_guessed_but_a_rate_can_be_back_solved_and_flagged():
    st = L.loan_state(facts(rate_pct=None, principal_c=None, start=None), D(2026, 10, 5))
    assert st["missing_for_amortization"] == ["principal", "rate.nominal", "start_date"] and st["amortization"] is None
    assert st["remaining_capital"] == "180000.00" and "declared" in st["remaining_capital_source"]
    imp = st["implied_rate"]                                       # 180,000.00 and 1,276.58 over 159 months
    assert imp["approximate"] and 0 < imp["nominal_pct"] < 10 and "Confirm" in imp["note"]
    nothing = L.loan_state(L.LoanFacts("x", "mortgage"), D(2026, 10, 5))
    assert nothing["missing_for_amortization"] == ["principal", "rate.nominal", "start_date", "end_date"]
    assert nothing["remaining_capital"] is None and "implied_rate" not in nothing
    bad = L.loan_state(facts(end=D(2019, 1, 1)), D(2026, 10, 5))
    assert bad["amortization"]["status"] == "invalid"


# ---------------------------------------------------------------- savings_estimate

def test_savings_estimate_hand_computed():
    e = savings_estimate(50, 35, 60, 12)
    # 15.00 a month ; 180.00 a year ; gross 180.00 ; net 120.00 ; break-even ceil(60 / 15) = 4
    assert (e.monthly_saving_c, e.yearly_saving_c, e.gross_saving_c, e.net_saving_c, e.break_even_months) == (1500, 18000, 18000, 12000, 4)
    assert e.verdict == "saving"
    d = e.to_dict()
    assert d["monthly_saving"] == "15.00" and d["net_saving"] == "120.00" and "verify" in d["disclaimer"]


def test_savings_estimate_edge_cases():
    long = savings_estimate(50, 45, 100, 12)                       # 5.00 a month, costs 100.00: pays back after 20 months
    assert long.verdict == "costs_exceed_saving_over_the_period" and long.break_even_months == 20 and long.net_saving_c == 6000 - 10000
    assert any("break-even" in n for n in long.notes)
    worse = savings_estimate(30, 35, 0, 12)
    assert worse.verdict == "no_saving" and worse.break_even_months is None and worse.monthly_saving_c == -500
    free = savings_estimate(30, 20, 0, 6)
    assert free.break_even_months == 0 and free.net_saving_c == 6000
    cents = savings_estimate(12.99, 8.5, 0.5, 3)                  # decimals are exact: 4.49 x 3 - 0.50 = 12.97
    assert cents.monthly_saving_c == 449 and cents.net_saving_c == 1297
    for bad in ((10, 5, 0, 0), (10, 5, 0, 121), (-1, 5, 0, 12), (10, 5, -1, 12)):
        with pytest.raises(ValueError):
            savings_estimate(*bad)


# ---------------------------------------------------------------- cancellability (rules table)

TODAY = D(2026, 10, 5)


def can(kind, country="FR", **kw):
    return C.cancellability(C.Terms(kind=kind, **kw), TODAY, country)


def test_every_rule_has_a_law_a_country_and_applies_to_a_known_family():
    assert len(C.RULES) >= 14
    for rid, r in C.RULES.items():
        assert rid.startswith(("fr-", "it-")) and r["country"] == rid[:2].upper(), rid
        assert r["law"] and r["summary"] and r["name"], rid
        assert set(r["families"]) <= set(C.FAMILY.values()), rid
    names = " ".join(r["name"] + r["law"] for r in C.RULES.values())
    for needle in ("Hamon", "Chatel", "Lemoine", "Bersani", "ARERA"):
        assert needle in names, needle
    assert {r["country"] for r in C.rules_table("IT")} == {"IT"} and len(C.rules_table()) == len(C.RULES)


def test_fr_loi_hamon_car_and_home_insurance():
    old = can("insurance_car", start_date=D(2025, 1, 10), renewal=D(2027, 1, 10))
    assert old["can_cancel_now"] is True and old["earliest_effective_date"] == D(2026, 11, 5) and old["notice_period_days"] == 30
    assert old["rules"][0]["id"] == "fr-hamon" and "3 clicks" in old["method"]
    assert old["anniversary_route"]["effective"] == D(2027, 1, 10) and old["anniversary_route"]["send_notice_by"] == D(2026, 11, 10)
    young = can("insurance_home", start_date=D(2026, 3, 1))
    assert young["can_cancel_now"] is False and young["first_request_date"] == D(2027, 3, 1)
    assert young["earliest_effective_date"] == D(2027, 4, 1) and any("anniversary" in c for c in young["conditions"])
    assert can("insurance_car")["can_cancel_now"] is None and can("insurance_car")["unknown"] == ["start_date"]


def test_a_start_date_that_is_only_the_first_payment_seen_never_proves_a_young_contract():
    sure = can("insurance_car", start_date=D(2025, 6, 1), start_is_lower_bound=True)
    assert sure["can_cancel_now"] is True                           # at least a year old whatever the real start
    maybe = can("insurance_car", start_date=D(2026, 6, 1), start_is_lower_bound=True)
    assert maybe["can_cancel_now"] is None and maybe["unknown"]


def test_fr_mutuelle_loan_insurance_telecom_energy():
    mut = can("insurance_health", start_date=D(2025, 9, 1))
    assert mut["can_cancel_now"] is True and mut["rules"][0]["id"] == "fr-ria-sante" and mut["earliest_effective_date"] == D(2026, 11, 5)
    grp = can("insurance_health", start_date=D(2020, 9, 1), group_contract=True)
    assert grp["can_cancel_now"] is False and any("group" in c for c in grp["conditions"])
    assert can("health", start_date=D(2025, 9, 1))["family"] == "insurance_health"
    lem = can("loan_insurance")
    assert lem["can_cancel_now"] is True and lem["lender_answer_working_days"] == 10 and lem["rules"][0]["id"] == "fr-lemoine"
    # inside its commitment a telecom contract CAN be left, at a price: after 12 months at most 25 % of the fees still due
    committed = can("telecom", start_date=D(2025, 6, 1), commitment_end=D(2027, 2, 1), monthly_fee_c=3000)
    cost = committed["early_termination_cost"]
    assert committed["can_cancel_now"] is True and committed["earliest_effective_date"] == D(2026, 10, 15)
    # 119 days left = 4 months (rounded up): 4 x 30.00 x 25 % = 30.00 ; waiting until 2027-02-01 is free
    assert (cost["remaining_months"], cost["amount"], cost["free_exit_date"]) == (4, 30.0, D(2027, 2, 1)) and "25 %" in cost["basis"]
    early = can("telecom", start_date=D(2026, 6, 1), commitment_end=D(2028, 6, 1), monthly_fee_c=3000)
    # first 12 months: the whole remainder: 20 months x 30.00 = 600.00
    assert early["early_termination_cost"]["amount"] == 600.0 and "first 12 months" in early["early_termination_cost"]["basis"]
    nofee = can("telecom", start_date=D(2025, 6, 1), commitment_end=D(2027, 2, 1))
    assert nofee["early_termination_cost"]["amount"] is None and "billing.amount" in nofee["early_termination_cost"]["needs"]
    assert can("telecom", commitment_end=D(2027, 2, 1), monthly_fee_c=3000)["early_termination_cost"]["amount"] is None      # age of the contract unknown
    free = can("telecom", start_date=D(2020, 1, 1))
    assert free["can_cancel_now"] is True and free["notice_period_days"] == 10 and free["earliest_effective_date"] == D(2026, 10, 15)
    en = can("energy", start_date=D(2025, 1, 1))
    assert en["can_cancel_now"] is True and en["rules"][0]["id"] == "fr-energy" and "new supplier" in en["method"]
    assert any("fixed-term" in c for c in can("energy", commitment_end=D(2027, 1, 1))["conditions"])


def test_fr_subscriptions_commitments_renewals_and_rolling():
    fixed = can("streaming", commitment_end=D(2027, 3, 1))
    assert fixed["can_cancel_now"] is False and fixed["earliest_effective_date"] == D(2027, 3, 1)
    renew = can("software", renewal=D(2027, 1, 15), notice_period_days=30)
    # notice 30 days before 2027-01-15 -> send by 2026-12-16, still possible
    assert renew["can_cancel_now"] is True and renew["earliest_effective_date"] == D(2027, 1, 15) and renew["send_notice_by"] == D(2026, 12, 16)
    late = can("software", renewal=D(2026, 10, 20), notice_period_days=30)         # the notice date has passed: next year
    assert late["can_cancel_now"] is False
    passed = can("software", renewal=D(2026, 3, 1), notice_period_days=30)         # renewed in March: next renewal 2027-03-01
    assert passed["earliest_effective_date"] == D(2027, 3, 1) and any("renewed" in c for c in passed["conditions"])
    ids = {r["id"] for r in passed["rules"]}
    assert {"fr-chatel-renewal", "fr-3-clics"} <= ids
    monthly = can("streaming", billing_period="monthly")
    assert monthly["can_cancel_now"] is True and any("end of the period" in c for c in monthly["conditions"])
    unk = can("other")
    assert unk["can_cancel_now"] is None and unk["unknown"] == ["renewal or commitment_end"]


def test_it_rules():
    tlc = can("telecom", "IT", start_date=D(2025, 1, 1), notice_period_days=60)
    assert tlc["can_cancel_now"] is True and tlc["notice_period_days"] == 30 and tlc["earliest_effective_date"] == D(2026, 11, 4)
    assert tlc["rules"][0]["id"] == "it-bersani-telecom"
    held = can("telecom", "IT", commitment_end=D(2027, 6, 1))
    assert held["can_cancel_now"] is False and held["earliest_effective_date"] == D(2027, 6, 1)
    en = can("energy", "IT")
    assert en["can_cancel_now"] is True and en["notice_period_days"] == 30 and en["rules"][0]["id"] == "it-bersani-energy"
    rca = can("insurance_car", "IT", renewal=D(2027, 2, 1))
    assert rca["can_cancel_now"] is False and rca["earliest_effective_date"] == D(2027, 2, 1) and rca["notice_period_days"] is None
    assert rca["rules"][0]["id"] == "it-rcauto"
    home = can("insurance_home", "IT", renewal=D(2027, 2, 1), notice_period_days=30)
    assert home["send_notice_by"] == D(2027, 1, 2) and home["earliest_effective_date"] == D(2027, 2, 1)
    assert can("insurance_home", "IT", renewal=D(2027, 2, 1))["unknown"] == ["notice_period_days"]
    assert can("loan_insurance", "IT")["can_cancel_now"] is True
    assert {r["id"] for r in can("streaming", "IT", billing_period="monthly")["rules"]} == {"it-subscription"}


def test_the_contractual_notice_deadline_and_the_disclaimer_are_always_given():
    r = can("software", renewal=D(2027, 1, 15), notice_period_days=45)
    d = r["contract_notice_deadline"]
    assert d["send_notice_by"] == D(2026, 12, 1) and d["days_left"] == 57 and "verify with your contract" in r["disclaimer"].lower()
    for kind in ("insurance_car", "telecom", "energy", "streaming", "loan_insurance", "other"):
        for country in ("FR", "IT"):
            out = can(kind, country)
            assert "Verify with your contract" in out["disclaimer"] and out["rules"], (kind, country)
    with pytest.raises(ValueError):
        C.cancellability(C.Terms(kind="energy"), TODAY, "DE")


# ---------------------------------------------------------------- synthetic contract files (the memory model)

def test_cancellability_of_synthetic_contract_files():
    from coach.memory import schemas as S
    car = S.Contract(id="car", provider="SafeCar", kind="insurance_car", start_date=D(2024, 3, 1), renewal=D(2027, 3, 1), notice_period_days=30)
    r = C.cancellability_of(car, TODAY, "FR")
    assert r["can_cancel_now"] is True and r["earliest_effective_date"] == D(2026, 11, 5)
    assert r["anniversary_route"]["send_notice_by"] == D(2027, 1, 1) and r["contract_notice_deadline"]["send_notice_by"] == D(2027, 1, 30)
    net = S.Contract(id="net", kind="telecom", start_date=D(2026, 8, 1), commitment_end=D(2028, 8, 1))
    n = C.cancellability_of(net, TODAY, "FR")
    assert n["can_cancel_now"] is True and n["early_termination_cost"]["free_exit_date"] == D(2028, 8, 1)
    assert n["early_termination_cost"]["amount"] is None                                   # no billing amount on file: the cost cannot be priced
    net2 = S.Contract(id="net2", kind="telecom", start_date=D(2026, 8, 1), commitment_end=D(2028, 8, 1), billing=S.Billing(amount=30, period="monthly"))
    assert C.cancellability_of(net2, TODAY, "FR")["early_termination_cost"]["amount"] == 660.0         # 22 months x 30.00 (first 12 months: all of it)
    power = S.Contract(id="power", kind="energy", billing=S.Billing(amount=90, period="monthly"))
    assert C.cancellability_of(power, TODAY, "IT")["notice_period_days"] == 30
    mutuelle = S.Contract(id="health", kind="health", start_date=D(2020, 1, 1))
    assert C.cancellability_of(mutuelle, TODAY, "FR", group_contract=True)["can_cancel_now"] is False
    assert C.cancellability_of(mutuelle, TODAY, "FR")["can_cancel_now"] is True
    rolling = S.Contract(id="gym", kind="other", billing=S.Billing(amount=30, period="monthly"))
    assert C.cancellability_of(rolling, TODAY, "FR")["can_cancel_now"] is True          # a rolling monthly subscription
    other = S.Contract(id="club", kind="other")
    assert C.cancellability_of(other, TODAY, "FR")["can_cancel_now"] is None            # nothing on file: the dates are unknown
    assert C.terms_of(car).kind == "insurance_car" and C.terms_of(power).billing_period == "monthly"


def test_the_household_country_is_part_of_the_schema():
    from coach.memory.store import MemoryStore
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(Path(d), history=False)
        ok, issues = store.validate_text("household.yaml", "members: []\ncountry: IT\n")
        assert ok is not None and not [i for i in issues if i.level == "error"]
        bad, issues = store.validate_text("household.yaml", "members: []\ncountry: DE\n")
        assert [i for i in issues if i.level == "error"]


# ---------------------------------------------------------------- review round: IRA on a prepayment, sources, corrected legal text

def test_prepayment_ira_is_six_months_of_interest_on_the_prepaid_amount_capped_at_three_percent_of_the_capital_before():
    # 20,000 prepaid on 100,000 at 7 %: 20,000 x 7 % / 2 = 700.00 (3 % of the 20,000 would wrongly give 600.00); cap 3 % x 100,000 = 3,000
    p = L.prepayment_effect(capital_c=10_000_000, amount_c=2_000_000, rate_pct=7, payment_c=None, remaining_months=240, ira_possible=True)
    assert p.penalty_estimate_c == 70_000
    # a big prepayment hits the cap of 3 % of the capital due before it: 90,000 at 7 % = 3,150.00 > 3,000.00
    big = L.prepayment_effect(capital_c=10_000_000, amount_c=9_000_000, rate_pct=7, payment_c=None, remaining_months=240, ira_possible=True)
    assert big.penalty_estimate_c == 300_000


def test_every_rule_carries_a_source_and_a_review_date_and_the_corrected_legal_texts():
    for r in C.rules_table():
        assert r["source"] and r["last_reviewed"] == "2026-10", r["id"]
    ins = C.RULES["fr-chatel-insurance"]
    assert "28 janvier 2005" in ins["law"] and "juillet" not in ins["law"] and "L113-15-1" in ins["law"]
    assert "20 days" in ins["summary"] and "15 days" in ins["summary"]
    assert "28 janvier 2005" in C.RULES["fr-chatel-renewal"]["law"]
    it = C.RULES["it-insurance"]["summary"]
    assert "60 days" in it and "end of the policy year" in it and "15 days" not in it
    assert "15 days" in C.RULES["it-rcauto"]["summary"] and "grace" in C.RULES["it-rcauto"]["summary"]
    home = can("insurance_home", "IT", renewal=D(2027, 2, 1), notice_period_days=30)
    assert any("60 days" in c for c in home["conditions"]) and not any("15 days" in c for c in home["conditions"])
    assert any("15 more days" in c for c in can("insurance_car", "IT", renewal=D(2027, 2, 1))["conditions"])
