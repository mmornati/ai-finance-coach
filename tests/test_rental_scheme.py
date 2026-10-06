"""E15-3: the scheme commitment (generic: the scheme name is data), its end date and reminders, the extension decision, the rent cap and tenant income
checks on the owner's OWN figures, the missing facts (never guessed), and the alert candidates built from the cards (local content; external
messages stay minimal). Synthetic property from ``rentalhelpers``."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from alerthelpers import ALL_ON, Net, settings
from coach.alerts import engine, messages as M, signals
from coach.analytics import api as analytics_api
from coach.memory import schemas as S
from coach.rental import cashflow as CF, model as M_, scheme as SC, service as RS
from rentalhelpers import SENTINELS, rental_world
from memhelpers import write

D = dt.date


def build(cfg, con, today):
    return analytics_api.build_dataset(con, cfg, today)


@pytest.fixture
def con(cfg):
    c = rental_world(cfg)
    yield c
    c.close()


def prop(ds):
    return M_.properties(ds)[0]


def test_the_end_date_is_the_day_before_the_anniversary_and_clamps_leap_days():
    assert SC.end_of(D(2021, 3, 5), 9) == D(2030, 3, 4)
    assert SC.end_of(D(2021, 1, 1), 6) == D(2026, 12, 31)
    assert SC.end_of(D(2024, 2, 29), 1) == D(2025, 2, 27)              # 29 Feb + 1 year: 28 Feb (clamped), minus a day
    assert SC.end_of(D(2024, 2, 29), 12) == D(2036, 2, 28)             # 2036 is a leap year: 29 Feb, minus a day
    assert SC.whole_months(D(2026, 10, 4), D(2030, 3, 4)) == 41 and SC.whole_months(D(2026, 10, 5), D(2030, 3, 4)) == 40


def test_the_status_of_an_active_commitment(cfg, con):
    ds = build(cfg, con, D(2026, 10, 4))
    s = SC.status(ds, prop(ds), 12)
    assert s["declared"] and s["scheme"] == "pinel" and s["state"] == "active"
    assert (s["start_date"], s["years"], s["end_date"], s["end_source"]) == (D(2021, 3, 5), 9, D(2030, 3, 4), "start + years")
    assert s["days_left"] == 1247 and s["months_left"] == 41 and s["progress_pct"] == 62.1 and s["decision_needed"] is False
    assert [(r["months_before"], r["date"]) for r in s["reminders"]] == [(12, D(2029, 3, 4)), (6, D(2029, 9, 4)), (3, D(2029, 12, 4))]
    assert s["next_reminder_date"] == D(2029, 3, 4) and s["extension"] == {"decision": "undecided"}


@pytest.mark.parametrize("today,severity", [(D(2029, 4, 1), "low"), (D(2029, 10, 1), "medium"), (D(2030, 1, 15), "high"), (D(2030, 5, 1), "high")])
def test_the_reminder_runs_while_no_extension_decision_is_recorded_and_grows_more_urgent(cfg, con, today, severity):
    ds = build(cfg, con, today)
    cards = [c for c in RS.cards(ds) if c["subtype"] == "scheme_end"]
    assert len(cards) == 1 and cards[0]["severity"] == severity and cards[0]["kind"] == "rental" and cards[0]["date"] == "2030-03-04"
    assert "extension" in cards[0]["body"] and "not tax advice" in cards[0]["body"]


def test_no_reminder_before_the_window_or_long_after_the_end(cfg, con):
    assert not [c for c in RS.cards(build(cfg, con, D(2028, 1, 1))) if c["subtype"] == "scheme_end"]
    assert not [c for c in RS.cards(build(cfg, con, D(2030, 11, 1))) if c["subtype"] == "scheme_end"]       # more than 6 months past the end


def test_a_recorded_decision_stops_the_reminders_and_an_extension_moves_the_end(cfg, con):
    store_text = (cfg.memory_dir / "assets.yaml").read_text()
    write(cfg.memory_dir / "assets.yaml", store_text.replace("      reduction_first_year: 2021\n", "      reduction_first_year: 2021\n"
                                                              "      extension:\n        decision: extend\n        years: 3\n        decided_on: 2029-06-01\n"))
    ds = build(cfg, con, D(2029, 10, 1))
    s = SC.status(ds, prop(ds), 12)
    assert s["extension"]["decision"] == "extend" and s["end_date"] == D(2030, 3, 4) and s["effective_end_date"] == D(2033, 3, 4)
    assert s["state"] == "active" and s["decision_needed"] is False
    assert not [c for c in RS.cards(ds) if c["subtype"] == "scheme_end"]
    ended = build(cfg, con, D(2033, 6, 1))
    assert SC.status(ended, prop(ended), 12)["state"] == "ended"


def test_the_rent_cap_and_the_tenant_income_use_the_owners_own_figures(cfg, con):
    ds = build(cfg, con, D(2026, 10, 4))
    s = SC.status(ds, prop(ds), 12)
    assert s["rent_cap"]["status"] == "above_cap" and s["rent_cap"]["gap_c"] == 2000 and s["rent_cap"]["cap_c"] == 60000 and s["rent_cap"]["rent_c"] == 62000
    assert s["tenant_income"]["status"] == "within_limit" and s["tenant_income"]["headroom_c"] == 200000
    cards = {c["subtype"]: c for c in RS.cards(ds)}
    assert cards["rent_cap"]["severity"] == "medium" and "above the cap you declared" in cards["rent_cap"]["title"] and "tenant_income" not in cards


def test_the_cap_can_come_from_a_cap_per_m2_and_the_surface(cfg, con):
    text = (cfg.memory_dir / "assets.yaml").read_text().replace("      rent_cap_monthly: 600\n", "      rent_cap_m2: 14.5\n")
    write(cfg.memory_dir / "assets.yaml", text)
    ds = build(cfg, con, D(2026, 10, 4))
    rc = SC.status(ds, prop(ds), 12)["rent_cap"]
    assert rc["cap_c"] == 58000 and rc["cap_basis"].startswith("the cap per m2") and rc["status"] == "above_cap"


def test_a_tenant_income_above_the_limit_is_flagged(cfg, con):
    write(cfg.memory_dir / "assets.yaml", (cfg.memory_dir / "assets.yaml").read_text().replace("tenant_income: 28000", "tenant_income: 31500"))
    ds = build(cfg, con, D(2026, 10, 4))
    t = SC.status(ds, prop(ds), 12)["tenant_income"]
    assert t["status"] == "above_limit" and t["headroom_c"] == -150000
    assert any(c["subtype"] == "tenant_income" for c in RS.cards(ds))


def test_missing_facts_are_listed_not_guessed(cfg):
    asset = """
  - id: rental-flat-1
    kind: real_estate_rental
    scheme: pinel
"""
    c = rental_world(cfg, asset=asset, loan=False)
    ds = build(cfg, c, D(2026, 10, 4))
    s = SC.status(ds, prop(ds), 12)
    fields = [m["field"] for m in s["missing"]]
    assert fields == ["loan", "value", "rent_monthly", "purchase_price", "purchase_date", "commitment.start_date", "commitment.years",
                      "commitment.rent_cap_monthly (or rent_cap_m2 and surface_m2)", "commitment.tenant_income_limit", "commitment.reduction_rate_pct"]
    assert s["state"] == "unknown" and s["end_date"] is None and s["rent_cap"]["status"] == "unknown" and s["tenant_income"]["status"] == "unknown"
    assert RS.cards(ds) == []                                                       # nothing to remind about, nothing invented
    c.close()


def test_a_property_without_a_scheme_is_not_asked_about_one(cfg):
    asset = """
  - id: rental-flat-1
    kind: real_estate_rental
    account: rn
    loan: rental-loan
    value: 100000
    rent_monthly: 600
    purchase_price: 90000
    purchase_date: 2019-05-01
"""
    c = rental_world(cfg, asset=asset)
    ds = build(cfg, c, D(2026, 10, 4))
    s = SC.status(ds, prop(ds), 12)
    assert not s["declared"] and s["missing"] == []
    c.close()


def test_the_legacy_pinel_fields_still_give_the_length(cfg):
    asset = """
  - id: rental-flat-1
    kind: real_estate_rental
    scheme: pinel
    pinel_commitment_years: 6
    commitment:
      start_date: 2020-01-01
"""
    c = rental_world(cfg, asset=asset)
    ds = build(cfg, c, D(2026, 10, 4))
    s = SC.status(ds, prop(ds), 12)
    assert s["years"] == 6 and s["end_date"] == D(2025, 12, 31) and s["state"] == "ended"
    c.close()


def test_an_unusual_pinel_length_is_a_warning_and_a_generic_scheme_name_is_data(cfg):
    asset = """
  - id: rental-flat-1
    kind: real_estate_rental
    scheme: pinel plus
    commitment: { start_date: 2022-01-01, years: 7 }
"""
    c = rental_world(cfg, asset=asset)
    ds = build(cfg, c, D(2026, 10, 4))
    s = SC.status(ds, prop(ds), 12)
    assert s["end_date"] == D(2028, 12, 31) and "6, 9 or 12" in s["warnings"][0]
    asset2 = asset.replace("pinel plus", "my-local-scheme").replace("years: 7", "years: 15")
    write(cfg.memory_dir / "assets.yaml", (cfg.memory_dir / "assets.yaml").read_text().split("  - id: rental-flat-1")[0] + asset2.lstrip("\n"))
    ds2 = build(cfg, c, D(2026, 10, 4))
    s2 = SC.status(ds2, prop(ds2), 12)
    assert s2["scheme"] == "my-local-scheme" and s2["years"] == 15 and "warnings" not in s2
    c.close()


def test_a_commitment_before_its_start_or_with_an_end_before_the_start_is_refused_or_not_started(cfg):
    with pytest.raises(Exception):
        S.Commitment(start_date="2022-01-01", end_date="2021-01-01")
    asset = """
  - id: rental-flat-1
    kind: real_estate_rental
    scheme: pinel
    commitment: { start_date: 2027-01-01, years: 9 }
"""
    c = rental_world(cfg, asset=asset)
    ds = build(cfg, c, D(2026, 10, 4))
    assert SC.status(ds, prop(ds), 12)["state"] == "not_started"
    c.close()


# ---------------------------------------------------------------- the missing rent

def test_a_month_without_rent_is_reported_once_the_data_cover_it(cfg, con):
    ds = build(cfg, con, D(2026, 6, 20))                                  # May is the last closed month: nothing explains it
    cards = [c for c in RS.cards(ds) if c["subtype"] == "rent_missing" and "no rent seen" in c["title"]]
    assert [c["title"] for c in cards] == ["rental-flat-1: no rent seen for 2026-05"] and cards[0]["amount"] == "620.00"
    again = [c["id"] for c in RS.cards(build(cfg, con, D(2026, 6, 25))) if c["subtype"] == "rent_missing" and "no rent seen" in c["title"]]
    assert again == [cards[0]["id"]]                                                                  # a stable id: one alert event, not one a day


def test_a_declared_vacancy_and_a_late_payment_are_not_missing_rents(cfg, con):
    ds = build(cfg, con, D(2026, 10, 4))
    assert not [c for c in RS.cards(ds) if c["subtype"] == "rent_missing"]            # Feb declared, Jun paid late, May is older than the last three months
    rows = {r.month: r.rent_status for r in CF.all_rows(ds, prop(ds))[0]}
    assert rows["2026-02"] == "declared_vacancy" and rows["2026-06"] == "late_paid" and rows["2026-05"] == "missing"


def test_this_months_rent_is_late_only_after_the_usual_day_plus_the_grace(cfg, con):
    pending = CF.current_month(build(cfg, con, D(2026, 10, 4)), prop(build(cfg, con, D(2026, 10, 4))), 7)
    assert pending["status"] == "pending"
    ds = build(cfg, con, D(2026, 10, 20))
    assert CF.current_month(ds, prop(ds), 7)["status"] == "late"
    late = [c for c in RS.cards(ds) if c["subtype"] == "rent_missing"]
    assert len(late) == 1 and late[0]["severity"] == "low" and "this month's rent has not arrived" in late[0]["title"]


# ---------------------------------------------------------------- the alert engine

def test_the_cards_become_alert_candidates_of_their_own_kinds(cfg, con):
    ds = build(cfg, con, D(2029, 10, 1))
    cards = RS.cards(ds)
    cands = signals.card_candidates(cards, settings())
    assert {c.kind for c in cands} == {"scheme_end", "scheme_check"}
    assert len({c.id for c in cands}) == len(cands)
    for k in ("scheme_end", "scheme_check", "rent_missing"):
        assert k in M.KIND_LABEL and k in __import__("coach.alerts.settings", fromlist=["KINDS"]).KINDS and k not in __import__("coach.alerts.settings", fromlist=["LOCAL_ONLY_KINDS"]).LOCAL_ONLY_KINDS


def test_an_external_message_about_the_commitment_is_minimal_and_holds_no_name(cfg, con):
    ds = build(cfg, con, D(2029, 10, 1))
    net = Net()
    s = settings(external_detail="summary", **{"ntfy": ALL_ON["ntfy"]})
    when = dt.datetime(2029, 10, 1, 12, 0, tzinfo=dt.timezone.utc)
    res = engine.run(con, cfg, ds, s=s, now=when, transports=net.transports())
    kinds = {e["kind"] for e in res["events"]}
    assert "scheme_end" in kinds
    local = json.dumps([[e["title"], e["body"]] for e in res["events"] if e["kind"] == "scheme_end"])
    assert "rental-flat-1" in local                                                        # the local feed names the property ...
    assert len(net.posts) == 1
    sent = net.posts[0]["body"]
    assert "rental-flat-1" not in sent and "Rental scheme commitment ending" in sent and "pinel" not in sent.lower()      # ... the external message does not
    low = sent.lower()
    assert not [x for x in SENTINELS if x in low] and "620" not in sent and "2030" not in sent


def test_a_persons_view_of_the_data_raises_no_rental_card(cfg, con):
    ds = build(cfg, con, D(2029, 10, 1))
    assert RS.cards(ds)                                            # the whole household sees the reminder ...
    assert RS.cards(ds.member_view("joint")) == []                 # ... a person's view (only what is attributed to them) never invents or hides one
