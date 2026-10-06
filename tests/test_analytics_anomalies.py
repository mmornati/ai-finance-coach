"""E4-5 anomalies: category spikes (robust z), duplicate charges, new merchants, large payments, dismissal."""

from anhelpers import D, make_ds, monthly, tx
from coach.analytics import anomalies as an
from coach.analytics.settings import AnalyticsSettings
from coach.db import connect


def groceries(amounts, start="2025-10-10", **kw):
    from coach.analytics.common import add_months
    return [tx(add_months(D(start), i), -a, "food.groceries", **kw) for i, a in enumerate(amounts)]


STEADY = [100, 105, 95, 100, 98, 102, 100, 101, 99, 100, 103]


def run(txs, **kw):
    return an.detect_anomalies(make_ds(txs, last_sync={"a": "2026-10-04"}, **kw))


def test_category_spike_is_flagged_with_evidence_and_severity():
    txs = groceries(STEADY + [600])                      # 2025-10 .. 2026-09; September is 600
    r = run(txs)
    (a,) = [x for x in r.anomalies if x.type == "category_spike"]
    assert (a.subject, a.period, a.amount_c, a.baseline_c) == ("food.groceries", "2026-09", 60000, 10000)
    assert a.severity == "high" and a.score >= 8 and a.evidence == [txs[-1].key]
    assert "600.00 EUR" in a.message and "100.00 EUR" in a.message
    assert r.counts["category_spike"] == 1 and r.counts["severity_high"] == 1 and txs[-1].key in r.evidence


def test_spike_needs_history_and_a_real_excess():
    assert run(groceries([100, 100, 100, 100, 600])).anomalies == []                      # too little history
    assert [x for x in run(groceries(STEADY + [130])).anomalies if x.type == "category_spike"] == []   # +30 %: ordinary
    small = groceries([5, 5, 6, 5, 4, 5, 5, 5, 5, 5, 5, 40])                              # z is huge but only 35 EUR more
    assert [x for x in run(small).anomalies if x.type == "category_spike"] == []


def test_intermittent_category_is_not_scored_and_never_high():
    other = [tx(m, -5.0, "leisure.hobbies") for m in ("2025-10-02", "2026-09-28")]       # keeps the account's history long
    hobby = [tx("2026-09-12", -300.0, "travel.activities")]
    r = run(groceries(STEADY + [100]) + other + hobby)
    a = next(x for x in r.anomalies if x.subject == "travel.activities" and x.type == "category_spike")
    assert a.severity == "low" and a.score is None and "usually nothing" in a.message


def test_one_offs_are_not_spikes():
    spike = groceries(STEADY + [100]) + [tx("2026-09-20", -900.0, "food.groceries", tags=["one_off"])]
    assert [x for x in run(spike).anomalies if x.type == "category_spike"] == []


def test_duplicate_charge_within_the_window_only():
    base = groceries([100] * 3)
    dup = [tx("2026-09-20", -45.0, "leisure.cinema_events", entity="CINEMAX", key="d1"),
           tx("2026-09-21", -45.0, "leisure.cinema_events", entity="CINEMAX", key="d2")]
    (a,) = [x for x in run(base + dup).anomalies if x.type == "duplicate_charge"]
    assert a.evidence == ["d1", "d2"] and a.amount_c == 4500 and a.severity == "medium" and a.baseline_c == 4500
    apart = [tx("2026-09-20", -45.0, "leisure.cinema_events", entity="CINEMAX"), tx("2026-09-25", -45.0, "leisure.cinema_events", entity="CINEMAX")]
    assert [x for x in run(base + apart).anomalies if x.type == "duplicate_charge"] == []
    small = [tx("2026-09-20", -3.0, "food.cafes_bars", entity="COFFEE"), tx("2026-09-20", -3.0, "food.cafes_bars", entity="COFFEE")]
    assert [x for x in run(base + small).anomalies if x.type == "duplicate_charge"] == []
    old = [tx("2026-02-20", -45.0, "leisure.cinema_events", entity="CINEMAX"), tx("2026-02-21", -45.0, "leisure.cinema_events", entity="CINEMAX")]
    assert [x for x in run(base + old).anomalies if x.type == "duplicate_charge"] == []      # older than 90 days
    big = [tx("2026-09-20", -150.0, "shopping.electronics", entity="TECHSHOP"), tx("2026-09-20", -150.0, "shopping.electronics", entity="TECHSHOP")]
    assert next(x for x in run(base + big).anomalies if x.type == "duplicate_charge").severity == "high"


def test_split_transaction_parts_are_not_duplicates():
    parts = [tx("2026-09-20", -50.0, "food.groceries", key="s1", split=0, source="split", entity="SUPERMARKET"),
             tx("2026-09-20", -50.0, "housing.maintenance_diy", key="s1", split=1, source="split", entity="SUPERMARKET")]
    assert [x for x in run(groceries([100] * 3) + parts).anomalies if x.type == "duplicate_charge"] == []


def test_new_merchant_with_a_large_amount():
    base = groceries([100] * 3)
    new = [tx("2026-09-20", -250.0, "shopping.clothing", entity="FANCYSHOP", key="n1")]
    (a,) = [x for x in run(base + new).anomalies if x.type == "new_merchant"]
    assert (a.subject, a.amount_c, a.evidence) == ("FANCYSHOP", 25000, ["n1"]) and a.severity == "medium"
    assert next(x for x in run(base + [tx("2026-09-20", -600.0, "shopping.clothing", entity="FANCYSHOP")]).anomalies
                if x.type == "new_merchant").severity == "high"
    assert [x for x in run(base + [tx("2026-09-20", -100.0, "shopping.clothing", entity="FANCYSHOP")]).anomalies
            if x.type == "new_merchant"] == []                                                      # under 150 EUR
    known = [tx("2026-01-05", -20.0, "shopping.clothing", entity="FANCYSHOP")]
    assert [x for x in run(base + known + new).anomalies if x.type == "new_merchant"] == []
    explained = [tx("2026-09-20", -2500.0, "housing.renovation", entity="BUILDER", tags=["one_off", "capital"])]
    assert [x for x in run(base + explained).anomalies if x.type == "new_merchant"] == []


def test_large_transaction_against_the_category_distribution():
    small = [tx(f"2026-0{m}-{d:02d}", -a, "transport.parking_tolls", entity=f"P{m}{d}")
             for m, d, a in [(3, 2, 12), (3, 9, 15), (4, 3, 14), (4, 10, 18), (5, 4, 16), (5, 11, 13), (6, 5, 20),
                             (6, 12, 15), (7, 4, 17), (7, 11, 14)]]
    known = [tx("2026-02-01", -14.0, "transport.parking_tolls", entity="TOLLCO")]
    big = [tx("2026-09-21", -180.0, "transport.parking_tolls", entity="TOLLCO", key="big1")]
    r = run(small + known + big)
    large = [x for x in r.anomalies if x.type == "large_transaction"]
    assert [x.evidence for x in large] == [["big1"]] and large[0].amount_c == 18000 and large[0].score >= 1
    assert large[0].baseline_c == 1500 and "180.00 EUR" in large[0].message              # median of the category
    assert [x for x in r.anomalies if x.type == "new_merchant"] == []                    # TOLLCO is a known merchant
    assert [x for x in run(small + known).anomalies if x.type == "large_transaction"] == []
    # the same payment from a merchant never seen before is reported once, as a new merchant
    r2 = run(small + [tx("2026-09-21", -180.0, "transport.parking_tolls", entity="NEWTOLL", key="big2")])
    assert {x.type for x in r2.anomalies if "big2" in x.evidence} == {"new_merchant"}


def test_recurring_payments_are_not_large_transactions():
    rent = monthly("2026-01-05", 9, -900.0, "housing.rent", entity="LANDLORD")
    small = [tx(f"2026-0{m}-1{m}", -50.0, "housing.rent", entity=f"X{m}") for m in range(1, 5)]
    r = run(rent + small)
    assert [x for x in r.anomalies if x.type == "large_transaction"] == []


def test_ids_are_stable_and_output_is_ordered_by_severity():
    txs = groceries(STEADY + [600]) + [tx("2026-09-20", -45.0, "leisure.cinema_events", entity="CINEMAX")] * 2
    r1, r2 = run(list(txs)), run(list(reversed(txs)))
    assert [x.id for x in r1.anomalies] == [x.id for x in r2.anomalies]
    sev = [x.severity for x in r1.anomalies]
    assert sev == sorted(sev, key=["high", "medium", "low"].index)
    assert r1.to_dict() == run(list(txs)).to_dict()


def test_thresholds_are_configurable():
    spikes = lambda txs, **kw: [x for x in run(txs, **kw).anomalies if x.type == "category_spike"]    # noqa: E731
    assert len(spikes(groceries(STEADY + [150]))) == 1                                                # z = 5
    assert spikes(groceries(STEADY + [150]), settings=AnalyticsSettings(anomaly_z=6.0)) == []
    assert spikes(groceries(STEADY + [125])) == []                                                    # z = 2.5, +25 EUR
    loose = AnalyticsSettings(anomaly_z=2.0, anomaly_min_excess=20.0, anomaly_min_ratio=1.1)
    assert len(spikes(groceries(STEADY + [125]), settings=loose)) == 1


def test_dismissal_survives_refresh_and_reset(cfg):
    con = connect(cfg, insecure=True, create=True)
    ds = make_ds(groceries(STEADY + [600]), last_sync={"a": "2026-10-04"})
    s1 = an.refresh_anomalies(con, ds, now_iso="2026-10-04T10:00:00+00:00")
    assert (s1.found, s1.new, s1.open) == (1, 1, 1)
    aid = s1.new_ids[0]
    assert an.dismiss_anomaly(con, aid, "known: holiday party", now_iso="2026-10-05T10:00:00+00:00") is True
    assert an.dismiss_anomaly(con, "anm_unknown") is False
    s2 = an.refresh_anomalies(con, ds)
    assert (s2.new, s2.open, s2.dismissed) == (0, 0, 1)
    stored = an.stored_anomalies(con, include_dismissed=True)
    assert stored[0]["dismissed"] is True and stored[0]["dismiss_note"] == "known: holiday party"
    assert an.stored_anomalies(con) == []
    hidden = an.detect_anomalies(ds, dismissed=frozenset({aid}))
    assert hidden.anomalies == [] and hidden.dismissed == 1
    shown = an.detect_anomalies(ds, dismissed=frozenset({aid}), include_dismissed=True)
    assert shown.anomalies[0].dismissed is True
    # the anomaly disappears from the data: a dismissed row is kept, an open one would be dropped
    an.refresh_anomalies(con, make_ds(groceries(STEADY + [100]), last_sync={"a": "2026-10-04"}))
    assert con.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0] == 1
    assert an.undismiss_anomaly(con, aid) is True
    an.refresh_anomalies(con, make_ds(groceries(STEADY + [100]), last_sync={"a": "2026-10-04"}))
    assert con.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0] == 0


def test_first_seen_is_kept_and_new_ids_only_for_new_anomalies(cfg):
    con = connect(cfg, insecure=True, create=True)
    ds = make_ds(groceries(STEADY + [600]), last_sync={"a": "2026-10-04"})
    an.refresh_anomalies(con, ds, now_iso="2026-10-04T10:00:00+00:00")
    again = an.refresh_anomalies(con, ds, now_iso="2026-10-09T10:00:00+00:00")
    assert again.new == 0 and again.new_ids == []
    assert con.execute("SELECT first_seen, last_seen FROM anomalies").fetchone() == ("2026-10-04T10:00:00+00:00", "2026-10-04T10:00:00+00:00")
