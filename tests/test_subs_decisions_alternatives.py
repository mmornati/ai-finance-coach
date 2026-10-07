"""E8-4 (alternatives store, staleness, savings by code) and E8-6 (decisions, verification against the bank data, realised savings).
Synthetic data only; every expected figure is hand-computed in the comment next to it."""
from __future__ import annotations

import datetime as dt

import pytest

from coach.skills.savings import savings_estimate
from coach.subs import alternatives as A, decisions as DEC, service as S
from helpers import add_tx
from memhelpers import label, monthly
from subshelpers import TODAY, build_subs_world


def D(s):
    return dt.date.fromisoformat(s)


@pytest.fixture
def world(cfg):
    con = build_subs_world(cfg)
    # DEALCO: 19.99 for seven months (2025-12 .. 2026-06) then 14.99 (2026-07 .. 2026-09): a renegotiated price
    monthly(con, "fo", "dl", "DEALCO", [-19.99] * 7 + [-14.99] * 3, start=(2025, 12), day=5)
    label(con, "DEALCO", "subscriptions.software_cloud")
    con.commit()
    yield con
    con.close()


def bundle(con, cfg, today=TODAY):
    return S.load_bundle(con, cfg, today, include_ended=True)


def ref(b, name):
    return next(r for r in b.inv["rows"] if r["name"] == name)


# ---------------------------------------------------------------- E8-4 validation

def ok(**kw):
    base = dict(provider="CheapStream", offer_name="Basic", monthly_price=8.99, retrieved_at="2026-09-20", source_url="https://example.org/offers",
                today=TODAY)
    base.update(kw)
    return A.validate(**base)


def test_validation_rules():
    v = ok()
    assert v["monthly_price_c"] == 899 and v["retrieved_at"] == D("2026-09-20") and v["source_url"] == "https://example.org/offers"
    for bad, msg in ((dict(monthly_price=0), "greater than 0"), (dict(monthly_price=-3), "greater than 0"),
                     (dict(retrieved_at="2026-10-05"), "in the future"), (dict(retrieved_at="yesterday"), "YYYY-MM-DD"),
                     (dict(source_url="http://example.org/offers"), "https"), (dict(source_url="https://127.0.0.1/x"), "host name"),
                     (dict(source_url="https://user:pw@example.org/x"), "public https"), (dict(source_url="https://localhost/x"), "public https"),
                     (dict(source_url="https://example.org/a b"), "not valid"), (dict(provider=" "), "required"),
                     (dict(offer_name="x" * 130), "longer than 120"), (dict(method="web"), "method"), (dict(switching_costs=-1), "between 0"),
                     (dict(monthly_price="abc"), "number")):
        with pytest.raises(A.AlternativeError, match=msg):
            ok(**bad)
    with pytest.raises(A.AlternativeError, match="required"):
        ok(source_url=None, require_url=True)               # the coach path always needs the source
    assert ok(source_url=None)["source_url"] is None        # a manual entry may have none
    assert ok(retrieved_at=TODAY)["retrieved_at"] == TODAY  # today is not the future


def test_the_coach_source_requires_a_url_and_a_manual_one_does_not(world, cfg):
    sb = ref(bundle(world, cfg), "StreamBox")
    with pytest.raises(A.AlternativeError, match="source URL"):
        A.add(world, series_id=sb["series_id"], today=TODAY, provider="X", offer_name="Y", monthly_price=5, retrieved_at="2026-10-01",
              method="find-cheaper", source="coach-llm")
    a = A.add(world, series_id=sb["series_id"], today=TODAY, provider="X", offer_name="Y", monthly_price=5, retrieved_at="2026-10-01", source="cli")
    assert a.id.startswith("alt_") and a.monthly_price_c == 500 and a.source == "cli" and a.source_url is None
    with pytest.raises(A.AlternativeError, match="which subscription"):
        A.add(world, today=TODAY, provider="X", offer_name="Y", monthly_price=5, retrieved_at="2026-10-01")


# ---------------------------------------------------------------- E8-4 staleness, the current best, savings by code

def test_staleness_boundary_and_savings_computed_by_code(world, cfg):
    sb = ref(bundle(world, cfg), "StreamBox")                                   # pays 12.99 a month
    A.add(world, series_id=sb["series_id"], today=TODAY, provider="CheapStream", offer_name="Basic", monthly_price=8.99, retrieved_at="2026-09-04",
          source_url="https://example.org/a", method="find-cheaper", source="cli")             # exactly 30 days old: still current
    A.add(world, series_id=sb["series_id"], today=TODAY, provider="OldStream", offer_name="Promo", monthly_price=4.99, retrieved_at="2026-09-03",
          source_url="https://example.org/b", method="manual", source="cli")                   # 31 days old: outdated
    al = ref(bundle(world, cfg), "StreamBox")["alternatives"]
    items = {i["provider"]: i for i in al["items"]}
    assert items["CheapStream"]["age_days"] == 30 and items["CheapStream"]["status"] == "current" and not items["CheapStream"]["stale"]
    assert items["OldStream"]["age_days"] == 31 and items["OldStream"]["status"] == "outdated" and items["OldStream"]["label"] == "outdated, re-check"
    # 12.99 - 8.99 = 4.00 a month = 48.00 a year, no switching costs: net over 12 months 48.00, break-even 0 months
    assert items["CheapStream"]["savings"] == {"monthly": "4.00", "yearly": "48.00", "net_12m": "48.00", "break_even_months": 0,
                                               "verdict": "saving", "computed_by": "code (savings_estimate)", "stale_warning": None}
    # the outdated one is cheaper (8.00 a month) but is shown with a warning and is NOT the current best
    assert items["OldStream"]["savings"]["monthly"] == "8.00" and "re-check" in items["OldStream"]["savings"]["stale_warning"]
    assert al["best"]["provider"] == "CheapStream" and (al["count"], al["current"], al["outdated"]) == (2, 1, 1)
    assert "outdated" in al["note"]


def test_best_is_the_fresh_quote_with_the_highest_net_saving_after_switching_costs(world, cfg):
    sb = ref(bundle(world, cfg), "StreamBox")
    kw = dict(series_id=sb["series_id"], today=TODAY, retrieved_at="2026-10-01", source_url="https://example.org/x", source="cli")
    A.add(world, provider="A", offer_name="cheap but costly", monthly_price=9.99, switching_costs=15, **kw)    # saves 3.00/mo: 36 - 15 = 21.00 net, break-even 5
    A.add(world, provider="B", offer_name="dearer, free", monthly_price=11.49, **kw)                          # saves 1.50/mo: 18.00 net
    A.add(world, provider="C", offer_name="not cheaper", monthly_price=14.00, **kw)                           # costs 1.01 more: no saving
    al = ref(bundle(world, cfg), "StreamBox")["alternatives"]
    by = {i["provider"]: i["savings"] for i in al["items"]}
    assert (by["A"]["net_12m"], by["A"]["break_even_months"]) == ("21.00", 5) and by["B"]["net_12m"] == "18.00"
    assert by["C"]["verdict"] == "no_saving" and by["C"]["monthly"] == "-1.01"
    assert al["best"]["provider"] == "A"
    # dropping the best leaves the next one; with only unhelpful quotes there is no best
    for i in al["items"]:
        if i["provider"] in ("A", "B"):
            A.remove(world, i["id"])
    assert ref(bundle(world, cfg), "StreamBox")["alternatives"]["best"] is None


def test_the_savings_of_an_alternative_equal_the_pure_calculator(world, cfg):
    sb = ref(bundle(world, cfg), "StreamBox")
    A.add(world, series_id=sb["series_id"], today=TODAY, provider="A", offer_name="o", monthly_price=7.50, switching_costs=20, retrieved_at="2026-10-02",
          source_url="https://example.org/x", source="cli")
    got = ref(bundle(world, cfg), "StreamBox")["alternatives"]["items"][0]["savings"]
    est = savings_estimate(12.99, 7.50, 20, 12)
    assert (got["monthly"], got["yearly"], got["net_12m"], got["break_even_months"]) == ("5.49", "65.88", "45.88", 4)       # ceil(20 / 5.49) = 4
    assert (est.monthly_saving_c, est.yearly_saving_c, est.net_saving_c, est.break_even_months) == (549, 6588, 4588, 4)


def test_alternatives_attach_to_a_series_or_a_contract_and_survive_a_series_rename(world, cfg):
    b = bundle(world, cfg)
    tel = ref(b, "TelcoCo")                                                       # a series WITH a contract file
    A.add(world, contract_id="telco", today=TODAY, provider="Other", offer_name="Plan", monthly_price=19.99, retrieved_at="2026-10-01",
          source_url="https://example.org/p", source="cli")
    again = ref(bundle(world, cfg), "TelcoCo")["alternatives"]
    assert again["count"] == 1 and again["best"]["savings"]["monthly"] == "10.00"     # 29.99 - 19.99
    assert A.load(world, contract_id="telco") == A.load(world, series_id="nonexistent", contract_id="telco") and tel["contract_id"] == "telco"


def test_the_inventory_counts_outdated_alternatives(world, cfg):
    sb = ref(bundle(world, cfg), "StreamBox")
    A.add(world, series_id=sb["series_id"], today=TODAY, provider="Old", offer_name="o", monthly_price=5, retrieved_at="2026-01-01", source="cli")
    assert bundle(world, cfg).inv["totals"]["outdated_alternatives"] == 1


# ---------------------------------------------------------------- E8-6 decisions

def test_decision_validation(world, cfg):
    kw = dict(today=TODAY, series_id="rec_x", before=10)
    for bad, msg in ((dict(decision="dropped"), "decision must be"), (dict(decision="cancelled", after=3), "costs 0"),
                     (dict(decision="kept", after=4), "costs the same"), (dict(decision="renegotiated"), "after"),
                     (dict(decision="renegotiated", after=10), "equal"), (dict(decision="cancelled", decided_on="2026-10-05"), "future"),
                     (dict(decision="cancelled", decided_on="2026-09-01", effective_on="2026-08-01"), "before decided_on"),
                     (dict(decision="cancelled", source="web"), "source"), (dict(decision="cancelled", before=None), "before"),
                     (dict(decision="cancelled", series_id=None), "which subscription"), (dict(decision="cancelled", decided_on="soon"), "YYYY-MM-DD"),
                     (dict(decision="switched", after=-1), ">= 0")):
        with pytest.raises(DEC.DecisionError, match=msg):
            DEC.check(**{**kw, **bad})
    v = DEC.check(**kw, decision="cancelled")
    assert (v["before_c"], v["after_c"], v["state"]) == (1000, 0, "confirmed")
    assert DEC.check(**kw, decision="cancelled", source="coach-llm")["state"] == "proposed"       # the coach only proposes


def decide(con, cfg, name, decision, today=TODAY, **kw):
    b = bundle(con, cfg, today)
    r = ref(b, name)
    return DEC.add(con, decision=decision, today=today, contract_id=r["contract_id"], series_id=r["series_id"], name=name, **kw)


def test_a_cancelled_series_that_stopped_is_verified_and_counted(world, cfg):
    d = decide(world, cfg, "Oldapp", "cancelled", decided_on="2026-01-10", before=3.99, source="cli")      # last payment 2026-01-08, the series ended
    s = DEC.savings([d], bundle(world, cfg).rec, TODAY)
    r = s["decisions"][0]
    assert r["status"] == "verified" and "no payment since 2026-01-08" in r["reason"] and "ended" in r["reason"]
    # 3.99 a month saved; whole months from 2026-01-10 to 2026-10-04 = 8 (2026-09-10 <= today < 2026-10-10): 8 x 3.99 = 31.92
    assert (r["monthly_saving"], r["months_counted"], r["since_decision"]) == ("3.99", 8, "31.92")
    assert (s["realised_monthly"], s["realised_since_decisions"], s["realised_yearly_run_rate"]) == ("3.99", "31.92", "47.88")
    assert (s["verified"], s["pending"], s["contradicted"]) == (1, 0, 0)


def test_a_cancelled_series_that_is_still_paid_is_contradicted_and_not_counted(world, cfg):
    d = decide(world, cfg, "Fitclub", "cancelled", decided_on="2026-08-20", effective_on="2026-09-01", before=39.90, source="cli")
    r = DEC.savings([d], bundle(world, cfg).rec, TODAY)
    row = r["decisions"][0]
    assert row["status"] == "contradicted" and "1 payment(s) after the effective date 2026-09-01; latest 2026-09-12 (39.90)" in row["reason"]
    assert r["realised_monthly"] == "0.00" and r["contradicted"] == 1


def test_a_payment_within_the_billing_grace_does_not_contradict(world, cfg):
    # effective 2026-09-10: the payment of 2026-09-12 is 2 days later = billing delay (grace 5 days); the next one (2026-10-12) is not due yet
    d = decide(world, cfg, "Fitclub", "cancelled", decided_on="2026-09-08", effective_on="2026-09-10", before=39.90, source="cli")
    v = DEC.verify(d, bundle(world, cfg).rec, TODAY)
    assert v["status"] == "pending" and v["check_on"] == D("2026-10-20")          # max(effective, next expected 10-12 + 7 days = 10-19) + 1 day


def test_pending_becomes_verified_when_the_expected_payment_does_not_come(world, cfg):
    d = decide(world, cfg, "Cloudbox", "cancelled", decided_on="2026-10-03", before=5.99, source="cli")
    rec_now = bundle(world, cfg).rec
    v = DEC.verify(d, rec_now, TODAY)
    # last payment 2026-09-06, next expected 2026-10-06 + 7 days: not yet overdue on 2026-10-04
    assert v["status"] == "pending" and v["check_on"] == D("2026-10-14") and "not overdue" in v["reason"]
    assert DEC.status_row(d, rec_now, TODAY)["reminder"] is False
    later = D("2026-10-20")                                                       # no payment on 2026-10-06 in the data: 14 days overdue
    b2 = bundle(world, cfg, later)
    v2 = DEC.verify(d, b2.rec, later)
    assert v2["status"] == "verified" and "next one is overdue" in v2["reason"]
    s = DEC.savings([d], b2.rec, later)
    assert s["realised_monthly"] == "5.99" and s["decisions"][0]["months_counted"] == 0 and s["realised_since_decisions"] == "0.00"   # 17 days: no whole month yet


def test_a_pending_decision_past_its_check_date_raises_a_reminder(world, cfg):
    # effective 2026-09-25, price cut to 2.99; no payment on or after that date is in the data: pending, to check from 2026-10-13
    # (next expected 2026-10-06 + 7 days)
    d = decide(world, cfg, "Cloudbox", "downgraded", decided_on="2026-09-20", effective_on="2026-09-25", before=5.99, after=2.99, source="cli")
    b = bundle(world, cfg)
    row = DEC.status_row(d, b.rec, TODAY)
    assert row["status"] == "pending" and row["check_on"] == D("2026-10-13") and row["reminder"] is False
    assert DEC.savings([d], b.rec, TODAY)["claimed_monthly_unverified"] == "3.00"
    later = D("2026-10-20")
    b2 = bundle(world, cfg, later)
    row2 = DEC.status_row(d, b2.rec, later)
    assert row2["status"] == "pending" and row2["reminder"] is True                         # the check date has passed and nothing confirms it
    from coach.analytics.upcoming import calendar_items
    b3 = S.load_bundle(world, cfg, later)
    assert any(i.kind == "decision_check" and i.date == later and "really downgraded" in i.title for i in calendar_items(b3.ds, 14, recurring=b3.rec).items)


def test_a_renegotiated_price_is_verified_by_the_next_payment(world, cfg):
    d = decide(world, cfg, "Dealco", "renegotiated", decided_on="2026-06-20", effective_on="2026-07-01", before=19.99, after=14.99, source="cli")
    b = bundle(world, cfg)
    s = DEC.savings([d], b.rec, TODAY)
    r = s["decisions"][0]
    assert r["status"] == "verified" and "2026-09-05 is 14.99 a month" in r["reason"]
    assert (r["monthly_saving"], r["months_counted"], r["since_decision"]) == ("5.00", 3, "15.00")        # 07-01 -> 10-01 = 3 whole months
    assert s["realised_monthly"] == "5.00" and s["realised_yearly_run_rate"] == "60.00"


def test_a_price_that_did_not_move_or_moved_elsewhere_is_contradicted(world, cfg):
    b = bundle(world, cfg)
    unchanged = decide(world, cfg, "Fitclub", "renegotiated", decided_on="2026-07-20", effective_on="2026-08-01", before=39.90, after=29.90, source="cli")
    assert DEC.verify(unchanged, b.rec, TODAY)["status"] == "contradicted" and "still about the old amount (39.90 a month)" in DEC.verify(unchanged, b.rec, TODAY)["reason"]
    elsewhere = decide(world, cfg, "Dealco", "downgraded", decided_on="2026-06-20", effective_on="2026-07-01", before=19.99, after=12.00, source="cli")
    v = DEC.verify(elsewhere, b.rec, TODAY)
    assert v["status"] == "contradicted" and "is 14.99 a month, not the 12.00 recorded" in v["reason"]       # 2 % of 12.00 = 0.24: 14.99 is outside
    near = decide(world, cfg, "Dealco", "downgraded", decided_on="2026-06-20", effective_on="2026-07-01", before=19.99, after=15.10, source="cli")
    assert DEC.verify(near, b.rec, TODAY)["status"] == "verified"                  # |14.99 - 15.10| = 0.11 <= 2 % of 15.10 (0.30)


def test_a_yearly_series_price_is_compared_as_a_monthly_equivalent():
    assert DEC.monthly_equivalent_c(-12000, "yearly") == 1000 and DEC.monthly_equivalent_c(3000, "quarterly") == 1000 and DEC.monthly_equivalent_c(-1299, "monthly") == 1299


def test_kept_is_recorded_without_savings_or_verification(world, cfg):
    d = decide(world, cfg, "StreamBox", "kept", before=12.99, note="the kids use it", source="cli")
    s = DEC.savings([d], bundle(world, cfg).rec, TODAY)
    assert s["decisions"][0]["status"] == "not_applicable" and s["decisions"][0]["monthly_saving"] == "0.00" and s["realised_monthly"] == "0.00"
    assert (s["verified"], s["pending"], s["contradicted"]) == (0, 0, 0)


def test_whole_months_are_calendar_months():
    assert DEC.whole_months(D("2026-07-01"), D("2026-10-04")) == 3 and DEC.whole_months(D("2026-01-31"), D("2026-02-28")) == 1
    assert DEC.whole_months(D("2026-10-05"), D("2026-10-04")) == 0 and DEC.whole_months(D("2026-10-04"), D("2026-10-04")) == 0


def test_cumulative_savings_across_decisions_and_the_coach_proposal_gate(world, cfg):
    d1 = decide(world, cfg, "Oldapp", "cancelled", decided_on="2026-01-10", before=3.99, source="cli")                     # verified: 3.99, 8 months = 31.92
    d2 = decide(world, cfg, "Dealco", "renegotiated", decided_on="2026-06-20", effective_on="2026-07-01", before=19.99, after=14.99, source="cli")   # verified 5.00, 3 months = 15.00
    decide(world, cfg, "Fitclub", "cancelled", decided_on="2026-08-20", effective_on="2026-09-01", before=39.90, source="cli")             # contradicted
    decide(world, cfg, "Cloudbox", "cancelled", decided_on="2026-10-03", before=5.99, source="cli")                                      # pending
    # the coach proposes: stored as PROPOSED, not counted
    p = decide(world, cfg, "TelcoCo", "renegotiated", decided_on="2026-09-01", before=29.99, after=19.99, source="coach-llm")
    assert p.state == "proposed" and p.source == "coach-llm"
    b = bundle(world, cfg)
    s = DEC.savings(b.decisions, b.rec, TODAY)
    assert (s["verified"], s["pending"], s["contradicted"]) == (2, 1, 1)
    assert s["realised_monthly"] == "8.99" and s["realised_since_decisions"] == "46.92" and s["realised_yearly_run_rate"] == "107.88"      # 3.99 + 5.00; 31.92 + 15.00; x 12
    assert s["claimed_monthly_unverified"] == "5.99" and len(s["decisions"]) == 4 and d1.id and d2.id           # the proposed one is not in the list
    assert b.inv["savings"]["realised_monthly"] == "8.99" and b.inv["savings"]["verified"] == 2
    assert ref(b, "TelcoCo")["proposed_decisions"] == [p.id] and ref(b, "TelcoCo")["decision"] is None
    # confirming it (a human action) makes it count; rejecting closes it
    DEC.confirm(world, p.id)
    b = bundle(world, cfg)
    assert ref(b, "TelcoCo")["decision"]["decision"] == "renegotiated"
    with pytest.raises(DEC.DecisionError, match="not proposed"):
        DEC.confirm(world, p.id)
    q = decide(world, cfg, "Homesure Assurances", "switched", decided_on="2026-09-01", before=22.50, after=15.00, source="coach-llm")
    assert DEC.reject(world, q.id).state == "rejected" and q.id not in {d.id for d in DEC.load(world, ("confirmed", "proposed"))}


def test_decisions_feed_the_calendar_and_the_insights(world, cfg):
    from coach.analytics.upcoming import calendar_items
    from coach.subs import reminders
    decide(world, cfg, "Fitclub", "cancelled", decided_on="2026-08-20", effective_on="2026-09-01", before=39.90, source="cli")      # still paid
    decide(world, cfg, "Cloudbox", "downgraded", decided_on="2026-09-20", effective_on="2026-09-25", before=5.99, after=2.99, source="cli")
    b = S.load_bundle(world, cfg, TODAY)
    assert {d.decision for d in b.ds.decisions} == {"cancelled", "downgraded"}
    items = [i for i in calendar_items(b.ds, 30, recurring=b.rec).items if i.kind == "decision_check"]
    assert any("Fitclub" in i.title and "still being charged" in i.title and i.date == TODAY for i in items)
    cards = [c for c in reminders.subscription_cards(b.ds, b.rec) if c["subtype"] == "decision_check"]
    assert any("still charged after you cancelled" in c["title"] and c["severity"] == "high" for c in cards)
    for c in cards:                                   # i18n 4c: the body's message is the verification's reason_msg
        assert c["body_msg"]["code"].startswith("subs.decision.") and c["body_msg"]["text"] == c["body"]
        assert c["title_msg"]["code"].startswith("reminder.decision.")


# ---------------------------------------------------------------- review round: ambiguity, refunds, the first counted month

def fake_series(payments, status="ended", last=None, direction="out"):
    from types import SimpleNamespace
    from coach.analytics.recurring import Occurrence
    occ = [Occurrence(f"k{i}", D(d), c) for i, (d, c) in enumerate(payments)]
    return SimpleNamespace(id="rec_f", occurrences=occ, status=status, last_date=occ[-1].date, next_expected=None, cadence="monthly", direction=direction,
                           links=[SimpleNamespace(kind="contract", id="c1")])


def dec(**kw):
    base = dict(id="dec_f", contract_id=None, series_id="rec_f", name="F", decision="cancelled", decided_on=D("2026-03-02"), effective_on=None, before_c=1000,
                after_c=0, note=None, source="cli", state="confirmed", created_at="t", confirmed_at=None)
    base.update(kw)
    return DEC.Decision(**base)


def test_a_refund_after_a_cancellation_is_not_a_payment():
    from types import SimpleNamespace
    x = fake_series([("2026-02-08", -1000), ("2026-03-06", -1000), ("2026-04-20", 1000)])          # a refund of 10.00 on 2026-04-20
    rec = SimpleNamespace(series=[x])
    v = DEC.verify(dec(), rec, D("2026-10-04"))
    assert v["status"] == "verified" and v["evidence"]["last_payment"] == x.last_date              # not "still paid": the later amount is positive
    paid_again = fake_series([("2026-02-08", -1000), ("2026-03-06", -1000), ("2026-04-20", -1000)])
    assert DEC.verify(dec(), SimpleNamespace(series=[paid_again]), D("2026-10-04"))["status"] == "contradicted"


def test_savings_count_from_the_first_month_after_the_last_payment_in_the_grace_window():
    from types import SimpleNamespace
    x = fake_series([("2026-02-08", -1000), ("2026-03-06", -1000)])
    rec = SimpleNamespace(series=[x])
    d = dec(decided_on=D("2026-03-02"))                       # effective 2026-03-02, but March was still paid (the 6th, inside the 5-day grace): counting starts 2026-04-01
    assert DEC.counted_from(d, rec) == D("2026-04-01")
    r = DEC.status_row(d, rec, D("2026-10-04"))
    # 04-01 -> 10-01 = 6 whole months (counting from the effective date would give 7): 6 x 10.00
    assert (r["counted_from"], r["months_counted"], r["since_decision"]) == (D("2026-04-01"), 6, "60.00")
    # last payment in December: the next month is January of the next year
    x2 = fake_series([("2025-12-05", -1000)])
    assert DEC.counted_from(dec(decided_on=D("2025-12-01")), SimpleNamespace(series=[x2])) == D("2026-01-01")
    assert DEC.counted_from(dec(decision="renegotiated", after_c=500), rec) == D("2026-03-02")      # a price change starts at the effective date


def test_a_decision_on_a_contract_covering_several_series_is_ambiguous_until_a_series_is_named(world, cfg):
    from helpers import add_tx
    for i in range(12):                                   # a second FITCLUB amount: both series match one contract with the same bank label
        m = (10 + i - 1) % 12 + 1
        add_tx(world, "fo", f"fitb{i:02d}", f"{2025 + (10 + i - 1) // 12}-{m:02d}-20", -19.90, "FITCLUB", "direct_debit")
    world.commit()
    (cfg.memory_dir / "contracts" / "fit.yaml").write_text("id: fit\nprovider: Fit\nmerchant_match: '^FITCLUB'\ndocuments: []\nnotes: ''\n")
    b = bundle(world, cfg)
    assert len(DEC.linked_series(dec(contract_id="fit", series_id=None), b.rec)) == 2
    d = dec(contract_id="fit", series_id=None, decided_on=D("2026-08-20"), before_c=3990)
    v = DEC.verify(d, b.rec, TODAY)
    assert v["status"] == "ambiguous" and "covers 2 recurring series" in v["reason"]
    s = DEC.savings([d], b.rec, TODAY)
    assert s["pending"] == 1 and s["realised_monthly"] == "0.00" and s["decisions"][0]["reminder"] is True
    named = next(x.id for x in b.rec.series if x.entity == "Fitclub" and abs(x.expected_amount_c) == 3990)
    assert DEC.verify(dec(contract_id="fit", series_id=named, decided_on=D("2026-08-20"), before_c=3990), b.rec, TODAY)["status"] == "contradicted"


def test_the_ics_uid_of_a_reminder_does_not_change_with_the_day(cfg, world):
    from coach.analytics.upcoming import calendar_items, to_ics
    b = S.load_bundle(world, cfg, TODAY)
    a = to_ics(calendar_items(b.ds, 14, recurring=b.rec))
    b2 = S.load_bundle(world, cfg, D("2026-10-05"))
    c = to_ics(calendar_items(b2.ds, 14, recurring=b2.rec))
    import hashlib
    for kind in ("unused_reminder", "never_used_reminder"):
        ref = "streambox"
        assert f"UID:{hashlib.sha1(f'contract|{kind}|{ref}'.encode()).hexdigest()[:20]}@ai-finance-coach" in a and \
               f"UID:{hashlib.sha1(f'contract|{kind}|{ref}'.encode()).hexdigest()[:20]}@ai-finance-coach" in c
