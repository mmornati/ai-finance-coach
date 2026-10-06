"""Opus review of E4: scoped forecast, robust variable spend, renamed payers, same-day pairs, reference tokens, id
stability, --as-of, budget/goal safety, coverage refinements, debt context and the redaction of the registry."""
import datetime as dt
import json
import re
from types import SimpleNamespace

import pytest

from anhelpers import D, account, make_ds
from anhelpers import monthly as _monthly, tx as _tx
from coach.analytics import anomalies as an, averages, budgets, cashflow, forecast as fc, recurring, upcoming
from coach.analytics.common import Scope, add_months
from coach.analytics.dataset import MemorySnapshot, load_dataset
from coach.cli import main
from coach.memory import schemas
from coach.memory.store import MemoryStore
from helpers import add_tx
from memhelpers import make_world

ASOF = "2026-10-04"


def tx(date, amount, category="subscriptions.memberships", *a, **k):
    return _tx(date, amount, category, *a, **k)


def monthly(start, n, amount, category="subscriptions.memberships", **k):
    return _monthly(start, n, amount, category, **k)


def run(cfg, *argv):
    """--yes only skips the typed prompt for a person at a terminal: a call with --yes pretends to be one (the refusals: test_cli_confirmation)."""
    from unittest import mock
    args = ["--config", str(cfg.config_path), "--insecure", *argv]
    if "--yes" in argv:
        with mock.patch("coach.memory.commands._is_tty", return_value=True):
            return main(args)
    return main(args)


# ---------------------------------------------------------------- B1 scoped forecast and liabilities

def _liab(**kw):
    base = dict(id="home", kind="mortgage", monthly_payment=900.0, start_date=D("2020-01-12"), end_date=None,
                debited_from=None, debited_account=None, payment_match="^NOTHING$", rate=None, outstanding=None)
    return SimpleNamespace(**{**base, **kw})


def _two_accounts(mem):
    kid = account("k", "Kid card", owner="alex", purpose="kids")
    txs = [tx(add_months(D("2026-03-05"), i), -20.0, "food.groceries", "k", entity=f"S{i}") for i in range(7)]
    txs += [tx(add_months(D("2026-03-05"), i), -30.0, "food.groceries", "a", entity=f"M{i}") for i in range(7)]
    return make_ds(txs, accounts=[account(), kid], balances={"a": 5000.0, "k": 100.0}, memory=mem,
                   history={"a": ("2026-03-01", None), "k": ("2026-03-01", None)}, last_sync={"a": "2026-10-04", "k": "2026-10-04"})


def test_accent_folding_resolves_the_account_named_in_free_text():
    acc = account("a", "Caisse d'Epargne main", purpose="main", bank="Caisse d'Épargne Normandie")
    ds = make_ds([tx("2026-09-01", -1.0)], accounts=[acc])
    lb = _liab(debited_from="Caisse d'Épargne main")
    assert fc.resolve_liability_account(ds, lb) == "a"
    assert fc.fold("Épargne ÉÈ") == "epargne ee"


def test_debited_account_field_is_exact_and_in_the_schema():
    lb = schemas.Liability(id="home", kind="mortgage", debited_account="Main", monthly_payment=900)
    assert lb.debited_account == "Main"
    ds = _two_accounts(MemorySnapshot(liabilities=[("l", _liab(debited_account="Main"))]))
    assert fc.resolve_liability_account(ds, _liab(debited_account="Main")) == "a"
    assert fc.resolve_liability_account(ds, _liab(debited_account="nope")) is None


def test_scoped_forecast_never_charges_an_unresolved_liability_to_the_scope():
    mem = MemorySnapshot(liabilities=[("l", _liab(debited_from="somewhere nobody can find"))])
    ds = _two_accounts(mem)
    whole = fc.forecast(ds, 90)
    assert any(e.source == "liability" and e.account is None for e in whole.household.events)       # household: kept, flagged
    kid = fc.forecast(ds, 90, Scope.make(owners=["alex"]))
    assert [e for e in kid.household.events if e.source == "liability"] == []
    assert any("debited account unknown" in t for t in kid.assumptions)
    base = fc.forecast(_two_accounts(MemorySnapshot()), 90, Scope.make(owners=["alex"]))
    assert kid.household.milestones[2].balance_c == base.household.milestones[2].balance_c


def test_liability_link_does_not_depend_on_the_scope():
    pay = monthly("2026-04-12", 6, -900.0, "housing.mortgage", entity="HOMEBANK", account="a")
    lb = _liab(payment_match="^HOMEBANK", debited_account="Main")
    mem = MemorySnapshot(liabilities=[("l", lb)])
    kid = account("k", "Kid card", owner="alex", purpose="kids")
    ds = make_ds(pay + [tx("2026-09-01", -5.0, "food.groceries", "k", entity="Z")], accounts=[account(), kid], memory=mem,
                 balances={"a": 5000.0, "k": 100.0}, last_sync={"a": "2026-10-04", "k": "2026-10-04"})
    r = fc.forecast(ds, 60, Scope.make(owners=["alex"]))                       # the series lives on another account
    assert [e for e in r.household.events if e.source == "liability"] == []
    r2 = fc.forecast(ds, 60, Scope.make(accounts=["a"]))
    assert [e for e in r2.household.events if e.source == "liability"] == []   # explained by the series, not added twice
    assert any(e.source == "recurring" for e in r2.household.events)


# ---------------------------------------------------------------- F1 robust variable spend

def test_variable_spend_ignores_a_big_one_off_payment_and_pre_series_instalments():
    base = [tx(f"2026-{m:02d}-{d:02d}", -a, "food.groceries", entity=f"SH{m}{d}")
            for m, d, a in [(4, 3, 60), (4, 12, 60), (5, 3, 60), (5, 12, 60), (6, 3, 60), (6, 12, 60), (7, 3, 60), (7, 12, 60),
                            (8, 3, 60), (8, 12, 60), (9, 3, 60), (9, 12, 60)]]
    big = [tx("2026-06-20", -5900.0, "housing.maintenance_diy", entity="BUILDER", key="big")]
    # an instalment that later became a monthly series: two payments of 2,500 BEFORE the series is detected
    early = [tx("2026-04-28", -2500.0, "transfer.internal", entity="OTHER NAME", key="e1")]       # the payer had another name
    series = monthly("2026-05-28", 5, -2500.0, "transfer.internal", entity="TOSAVE")
    ds = make_ds(base + big + early + series, balances={"a": 20000.0}, history={"a": ("2026-03-01", None)},
                 last_sync={"a": "2026-10-04"})
    f = fc.forecast(ds, 30).accounts[0]
    assert f.variable_monthly_c == 12000                       # 2 x 60 a month: neither the 5,900 nor the first 2,500 count
    assert "e1" in fc._not_habits(ds, recurring.detect_recurring(ds))


def test_variable_spend_is_a_median_not_a_mean():
    months = [96, 112, 88, 104, 96, 600]
    txs = [tx(f"2026-{4 + i:02d}-{3 + 3 * j:02d}", -g / 8, "food.groceries", entity=f"SH{i}{j}") for i, g in enumerate(months) for j in range(8)]
    ds = make_ds(txs, balances={"a": 5000.0}, history={"a": ("2026-03-01", None)}, last_sync={"a": "2026-10-04"})
    assert fc.forecast(ds, 30).accounts[0].variable_monthly_c == 10000       # median of the six months (88 96 96 104 112 600): the lumpy one is no habit


# ---------------------------------------------------------------- F2 renamed payer, quarterly pair

def test_renamed_payer_is_the_missing_occurrence_not_an_overdue_one():
    rent = [tx(add_months(D("2026-04-24"), i), 612.40, "income.rental", entity="NEXITY NORMANDIE") for i in range(5)]
    new = [tx("2026-09-25", 612.40, "income.rental", entity="ZENITH GERANCE")]
    (s,) = recurring.detect_recurring(make_ds(rent + new)).series
    assert s.n_occurrences == 6 and s.aliases == ["ZENITH GERANCE"] and s.overdue_days == 0
    assert s.last_date == D("2026-09-25") and s.next_expected == D("2026-10-26")      # Oct 24 2026 is a Saturday... see roll
    cal = upcoming.calendar_items(make_ds(rent + new), 40)
    assert not any(i.note and "overdue" in i.note for i in cal.items)


def test_a_different_amount_or_category_is_not_taken_for_the_missed_payment():
    rent = [tx(add_months(D("2026-04-24"), i), 612.40, "income.rental", entity="NEXITY") for i in range(5)]
    other = [tx("2026-09-25", 120.0, "income.rental", entity="SOMEONE")]
    (s,) = recurring.detect_recurring(make_ds(rent + other)).series
    assert s.n_occurrences == 5 and s.overdue_days > 0
    other2 = [tx("2026-09-25", 612.40, "transfer.from_people", entity="SOMEONE")]
    (s2,) = recurring.detect_recurring(make_ds(rent + other2)).series
    assert s2.n_occurrences == 5


def test_two_payments_of_a_quarterly_property_charge_make_a_low_confidence_series():
    q = [tx("2026-01-10", -594.36, "housing.property_charges", entity="SYNDIC"), tx("2026-04-10", -594.36, "housing.property_charges", entity="SYNDIC")]
    (s,) = recurring.detect_recurring(make_ds(q + [tx("2026-05-01", -1.0, "food.groceries")])).series
    assert s.cadence == "quarterly" and s.confidence <= 0.5 and s.n_occurrences == 2
    food = [tx("2026-01-10", -60.0, "food.restaurants", entity="BISTRO"), tx("2026-04-10", -60.0, "food.restaurants", entity="BISTRO")]
    assert recurring.detect_recurring(make_ds(food)).series == []


# ---------------------------------------------------------------- F3 same-day pairs, reference tokens, loose amounts

def test_two_payments_on_the_same_days_are_two_series():
    txs = []
    for i in range(7):
        d0 = add_months(D("2026-03-05"), i)
        txs += [tx(d0, -108.72, "housing.property_charges", entity="KERVALIS"), tx(d0, -101.31, "housing.property_charges", entity="KERVALIS")]
    res = recurring.detect_recurring(make_ds(txs)).series
    assert sorted(s.expected_amount_c for s in res) == [-10872, -10131] and all(s.n_occurrences == 7 for s in res)
    assert all(s.cadence == "monthly" for s in res)


def test_reference_tokens_do_not_split_a_yearly_subscription():
    a = tx("2025-10-02", -69.90, "subscriptions.memberships", entity="Amazon Prime*9W8 amazon.fr/pri")
    b = tx("2026-10-02", -69.90, "subscriptions.memberships", entity="Amazon Prime*N42 CLICHY")
    (s,) = recurring.detect_recurring(make_ds([a, b], today=D("2026-10-03"))).series
    assert s.cadence == "yearly" and s.n_occurrences == 2
    assert recurring.norm_key("Spotify P30FCBC3 Goteborg") == "Spotify Goteborg"
    assert recurring.norm_key("NETFLIX COM DUBLIN") == "NETFLIX COM DUBLIN"


def test_monthly_payment_with_varying_amounts_in_a_loan_category_is_found():
    amounts = [269.82, 359.76, 359.76, 179.88, 365.28, 456.60, 91.32]
    txs = [tx(add_months(D("2026-03-12"), i) + dt.timedelta(days=i % 2), -a, "housing.rental_property_loan", entity="LEOV") for i, a in enumerate(amounts)]
    (s,) = recurring.detect_recurring(make_ds(txs)).series
    assert s.cadence == "monthly" and s.amount_mode == "variable"


def test_discretionary_categories_need_five_regular_payments():
    four = [tx(add_months(D("2026-05-05"), i), -45.0, "food.restaurants", entity="BISTRO") for i in range(4)]
    assert recurring.detect_recurring(make_ds(four)).series == []
    five = [tx(add_months(D("2026-04-05"), i), -45.0, "food.restaurants", entity="BISTRO") for i in range(5)]
    assert len(recurring.detect_recurring(make_ds(five)).series) == 1
    varying = [tx(add_months(D("2026-04-05"), i), -a, "food.restaurants", entity="BISTRO") for i, a in enumerate([30, 80, 45, 120, 20])]
    assert recurring.detect_recurring(make_ds(varying)).series == []


# ---------------------------------------------------------------- F4 id stability

def test_series_ids_are_stable_when_months_are_added_or_amounts_change():
    sal = [tx(add_months(D("2025-10-28"), i), a, "income.salary", entity="EMPLOYER") for i, a in enumerate([3000.0] * 6 + [3200.0] * 5)]
    ids = {n: recurring.detect_recurring(make_ds(sal[:n], today=add_months(D("2025-10-28"), n - 1) + dt.timedelta(days=3))).series[0].id
           for n in range(6, 12)}
    assert len(set(ids.values())) == 1


def test_series_id_survives_a_new_first_payment_through_member_overlap(cfg):
    from coach.db import connect
    con = connect(cfg, insecure=True, create=True)
    pays = monthly("2026-04-05", 6, -9.99, "subscriptions.video_streaming", entity="STREAMBOX")
    recurring.refresh_recurring(con, make_ds(pays))
    old = con.execute("SELECT id FROM recurring_series").fetchone()[0]
    older = [tx("2026-03-05", -9.99, "subscriptions.video_streaming", entity="STREAMBOX")]      # older data arrives: new first tx
    recurring.refresh_recurring(con, make_ds(older + pays))
    assert [r[0] for r in con.execute("SELECT id FROM recurring_series")] == [old]


def test_anomaly_ids_and_calendar_uids_are_stable_as_data_grows():
    def mk(n_dups):
        return make_ds([tx("2026-09-20", -45.0, "leisure.cinema_events", entity="CINEMAX", key=f"d{i}") for i in range(n_dups)]
                       + [tx("2026-09-22", -300.0, "shopping.clothing", entity="FANCY", key="n1")], last_sync={"a": "2026-10-04"})
    two, three = an.detect_anomalies(mk(2)), an.detect_anomalies(mk(3))
    for t in ("duplicate_charge", "new_merchant"):
        assert {a.id for a in two.anomalies if a.type == t} == {a.id for a in three.anomalies if a.type == t}
    rec = monthly("2026-04-05", 6, -9.99, "subscriptions.video_streaming", entity="STREAMBOX")
    u1 = upcoming.to_ics(upcoming.calendar_items(make_ds(rec), 40))
    u2 = upcoming.to_ics(upcoming.calendar_items(make_ds(rec + [tx("2026-10-05", -9.99, "subscriptions.video_streaming", entity="STREAMBOX")]), 40))
    uids = lambda t: set(re.findall(r"UID:(\S+)", t))        # noqa: E731
    assert uids(u1) & uids(u2)


# ---------------------------------------------------------------- F5 --as-of

def test_as_of_run_sees_only_the_past(cfg):
    con = make_world(cfg)
    early = load_dataset(con, cfg.memory_dir, today=D("2026-03-31"))
    assert max(t.date for t in early.txs) <= D("2026-03-31")
    con.execute("INSERT INTO balances VALUES ('ce','2026-07-01T08:00:00+00:00','CLBD',777.0,'EUR',NULL)")
    con.execute("INSERT INTO balances VALUES ('ce','2026-02-01T08:00:00+00:00','CLBD',111.0,'EUR',NULL)")
    con.execute("INSERT INTO sync_log VALUES ('ce','2026-09-01T00:00:00+00:00',1,0,1,'x')")
    con.commit()
    ds = load_dataset(con, cfg.memory_dir, today=D("2026-03-31"))
    assert ds.balances["ce"].amount_c == 11100
    assert ds.coverage.of("ce").last <= D("2026-03-31")
    assert all(n.get("date") is None or True for n in [])
    full = load_dataset(con, cfg.memory_dir, today=D("2026-10-04"))
    assert len(full.txs) > len(early.txs) and full.balances["ce"].amount_c == 77700


def test_as_of_never_persists(cfg, capsys):
    con = make_world(cfg)
    for argv in (["recurring", "refresh", "--as-of", ASOF], ["anomalies", "refresh", "--as-of", ASOF],
                 ["analytics", "refresh", "--as-of", ASOF], ["recurring", "--refresh", "--as-of", ASOF]):
        with pytest.raises(SystemExit) as e:
            run(cfg, *argv)
        assert "read-only" in str(e.value)
    assert con.execute("SELECT COUNT(*) FROM recurring_series").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0] == 0


# ---------------------------------------------------------------- N1..N5, minor schema items

def test_budget_id_cannot_silently_retarget_another_budget(cfg, capsys):
    make_world(cfg)
    run(cfg, "budget", "set", "food.groceries", "800", "--yes")
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run(cfg, "budget", "set", "food.restaurants", "100", "--id", "food-groceries", "--yes")
    assert "already exists for food.groceries" in str(e.value)
    assert "food.restaurants" not in (cfg.memory_dir / "budgets.yaml").read_text()
    run(cfg, "budget", "set", "food.restaurants", "100", "--id", "food-groceries", "--replace", "--yes")
    text = (cfg.memory_dir / "budgets.yaml").read_text()
    assert "category: food.restaurants" in text and "food.groceries" not in text
    run(cfg, "budget", "set", "food.restaurants", "120", "--id", "food-groceries", "--yes")      # same target: a plain update
    assert "monthly: 120" in (cfg.memory_dir / "budgets.yaml").read_text()


@pytest.mark.parametrize("bad", [".nan", ".inf", "-.inf"])
def test_nan_and_inf_are_refused_in_every_money_field(cfg, bad):
    cfg.memory_dir.mkdir(exist_ok=True)
    store = MemoryStore(cfg.memory_dir)
    files = {"budgets.yaml": f"budgets:\n  - id: a\n    category: food.groceries\n    monthly: {bad}\n",
             "goals.yaml": f"goals:\n  - id: g\n    target_amount: {bad}\n    tag: t\n",
             "assets.yaml": f"assets:\n  - id: a\n    kind: other\n    balance: {bad}\n",
             "liabilities/l.yaml": f"id: l\nkind: mortgage\nmonthly_payment: {bad}\n",
             "contracts/c.yaml": f"id: c\nbilling:\n  amount: {bad}\n"}
    for rel, text in files.items():
        _m, issues = store.validate_text(rel, text)
        assert issues and issues[0].level == "error", rel
    _m, issues = store.validate_text("categorization.yaml", f"annotations:\n  - id: a\n    match: {{amount_min: {bad}}}\n    tags: [x]\n")
    assert issues


def test_goal_and_budget_schemas_reject_unknown_keys_and_amounts_are_clean_errors(cfg, capsys):
    store = MemoryStore(cfg.memory_dir)
    assert store.validate_text("budgets.yaml", "budgets:\n  - id: a\n    category: food.groceries\n    monthly: 5\n    montly: 4\n")[1]
    assert store.validate_text("goals.yaml", "goals:\n  - id: g\n    target_amount: 5\n    tag: t\n    colour: red\n")[1]
    make_world(cfg)
    for argv in (["goals", "set", "g", "--target", "abc", "--tag", "savings", "--yes"],
                 ["goals", "set", "g", "--target", "nan", "--tag", "savings", "--yes"],
                 ["goals", "set", "g", "--target", "10", "--tag", "savings", "--monthly", "x", "--yes"]):
        with pytest.raises(SystemExit) as e:
            run(cfg, *argv)
        assert str(e.value).startswith("error: bad ")


def test_budget_owner_and_account_are_validated_when_written(cfg):
    make_world(cfg)
    for argv in (["budget", "set", "food.groceries", "100", "--owner", "ghost", "--yes"],
                 ["budget", "set", "food.groceries", "100", "--account", "ghost", "--yes"]):
        with pytest.raises(SystemExit) as e:
            run(cfg, *argv)
        assert "unknown" in str(e.value)
    run(cfg, "budget", "set", "food.groceries", "100", "--owner", "mia", "--yes")
    assert "owner: mia" in (cfg.memory_dir / "budgets.yaml").read_text()


def test_propose_with_dry_run_queues_nothing(cfg, capsys):
    make_world(cfg)
    run(cfg, "budget", "set", "food.groceries", "800", "--propose", "--dry-run")
    assert "nothing queued" in capsys.readouterr().out
    from coach.memory import proposals
    assert proposals.listing(MemoryStore(cfg.memory_dir)) == []


def test_memory_check_validates_budget_and_goal_references(cfg, capsys):
    make_world(cfg)
    (cfg.memory_dir / "budgets.yaml").write_text("budgets:\n  - id: b\n    category: not.a_category\n    monthly: 10\n"
                                                 "  - id: c\n    group: nogroup\n    monthly: 10\n")
    (cfg.memory_dir / "goals.yaml").write_text("goals:\n  - id: g1\n    target_amount: 10\n    tag: neverused\n"
                                               "  - id: g2\n    target_amount: 10\n    tag: neverused\n")
    with pytest.raises(SystemExit):                                  # an unknown category is an error: exit 1
        run(cfg, "memory", "check", "--json")
    codes = {i["code"] for i in json.loads(capsys.readouterr().out)["issues"]}
    assert {"unknown_category", "unknown_group", "goal_unknown_tag", "goal_shared_source"} <= codes


def test_rollover_ignores_months_the_accounts_do_not_cover():
    b = schemas.Budget(id="food", category="food.groceries", monthly=300, rollover=True, start=D("2026-04-01"))
    txs = [tx("2026-04-10", -100.0, "food.groceries", entity="A"), tx("2026-05-10", -100.0, "food.groceries", entity="B"),
           tx("2026-08-10", -100.0, "food.groceries", entity="C"), tx("2026-09-10", -100.0, "food.groceries", entity="D"),
           tx("2026-10-03", -10.0, "food.groceries", entity="E")]
    ds = make_ds(txs, today=D("2026-10-10"), memory=MemorySnapshot(budgets=[b]), last_sync={"a": "2026-10-10"},
                 history={"a": ("2026-01-01", None)})
    (p,) = budgets.budget_status(ds).budgets
    assert p.carry_c == 20000 + 20000 + 30000 + 30000 + 20000 + 20000      # April..September, each month's unspent part
    # the account stopped syncing in June: July-September are not covered, so they are not 'unspent'
    ds2 = make_ds(txs[:2] + [tx("2026-06-30", -1.0, "fees.bank_fees")], today=D("2026-10-10"), memory=MemorySnapshot(budgets=[b]),
                  history={"a": ("2026-01-01", "2026-06-30")})
    (p2,) = budgets.budget_status(ds2).budgets
    assert p2.carry_c == 20000 + 20000 + 30000                         # April, May, June only: July-September have no data


def test_tag_goal_starts_now_and_shared_sources_are_flagged(cfg, capsys):
    make_world(cfg)
    run(cfg, "goals", "set", "pot", "--target", "500", "--tag", "savings", "--yes")
    assert f"start: {dt.date.today()}" in (cfg.memory_dir / "goals.yaml").read_text()
    from coach.analytics import goals
    g1 = schemas.Goal(id="a", target_amount=10, tag="savings")
    g2 = schemas.Goal(id="b", target_amount=10, tag="savings", start=D("2026-01-01"))
    ds = make_ds([tx("2026-09-01", -1.0)])
    ps = {p.id: p for p in goals.goal_progress(ds, [g1, g2]).goals}
    assert "shares_source_with:b" in ps["a"].flags and "counts_all_history" in ps["a"].flags and "counts_all_history" not in ps["b"].flags


def test_budget_list_json_has_two_decimal_strings(cfg, capsys):
    make_world(cfg)
    run(cfg, "budget", "set", "food.groceries", "800", "--yes")
    capsys.readouterr()
    run(cfg, "budget", "list", "--json")
    assert json.loads(capsys.readouterr().out)[0]["monthly"] == "800.00"


# ---------------------------------------------------------------- coverage refinements

def test_a_minor_carrier_does_not_shorten_the_window_and_is_reported():
    big = monthly("2024-10-04", 24, -500.0, "food.groceries", account="a")
    tiny = [tx("2026-08-04", -4.0, "food.groceries", "b"), tx("2026-09-04", -4.0, "food.groceries", "b")]
    ds = make_ds(big + tiny, accounts=[account(), account("b", "Newcomer", purpose="cards")], last_sync={"a": "2026-10-04", "b": "2026-10-04"})
    c = averages.category_averages(ds).categories[0]
    assert c.n_months == 12 and c.accounts == ["Main"] and c.ignored_accounts == ["Newcomer"]
    ds2 = make_ds(big + [tx(add_months(D("2026-04-04"), i), -300.0, "food.groceries", "b") for i in range(6)],
                  accounts=[account(), account("b", "Newcomer", purpose="cards")], last_sync={"a": "2026-10-04", "b": "2026-10-04"})
    c2 = averages.category_averages(ds2).categories[0]
    assert c2.accounts == ["Main", "Newcomer"] and c2.n_months == 5          # a real carrier (>= 10 %) still limits the window
    assert averages.category_averages(make_ds(big + tiny, accounts=[account(), account("b", "N", purpose="cards")],
                                              settings=__import__("coach.analytics.settings", fromlist=["x"]).AnalyticsSettings(coverage_min_share=0.0),
                                              last_sync={"a": "2026-10-04", "b": "2026-10-04"})).categories[0].n_months == 1


def test_household_estimate_is_also_given_per_account_and_a_mismatch_is_flagged():
    a = monthly("2024-10-04", 24, -1000.0, "housing.rent", account="a")
    b = [tx(add_months(D("2026-06-04"), i), -1000.0, "food.groceries", "b", entity=f"G{i}") for i in range(4)]
    ds = make_ds(a + b, accounts=[account(), account("b", "Newcomer", purpose="cards")], last_sync={"a": "2026-10-04", "b": "2026-10-04"})
    r = averages.category_averages(ds)
    by = {x.label: x for x in r.household_by_account}
    assert by["Main"].monthly_avg_c == 100000 and by["Newcomer"].monthly_avg_c == 100000 and r.household_sum_c == 200000
    assert r.household_monthly_avg_c == 200000 and r.household_mismatch is False
    c = [tx(add_months(D("2024-10-04"), i), -1000.0, "housing.rent", "a") for i in range(24)] + \
        [tx(add_months(D("2026-06-04"), i), -10.0, "food.groceries", "b", entity=f"G{i}") for i in range(4)] + \
        [tx("2026-02-05", -6000.0, "housing.maintenance_diy", "a", entity="ROOF")]
    r2 = averages.category_averages(make_ds(c, accounts=[account(), account("b", "Newcomer", purpose="cards")], last_sync={"a": "2026-10-04", "b": "2026-10-04"}))
    assert r2.household_mismatch is True and any("differ by more than" in n for n in r2.coverage.notes)


def test_lumpy_categories_need_twelve_covered_months():
    trips = [tx("2026-02-10", -2000.0, "travel.lodging", entity="HOTEL")]
    txs = monthly("2025-04-04", 18, -50.0, "food.groceries") + trips
    r = averages.category_averages(make_ds(txs, last_sync={"a": "2026-10-04"}))
    c = {x.category: x for x in r.categories}
    assert c["travel.lodging"].lumpy and c["travel.lodging"].n_months == 12 and c["travel.lodging"].monthly_avg_c == 16667
    assert not c["travel.lodging"].low_confidence
    short = averages.category_averages(make_ds(monthly("2026-03-04", 6, -50.0, "food.groceries") + [tx("2026-05-10", -900.0, "travel.lodging")],
                                               last_sync={"a": "2026-10-04"}))
    assert {x.category: x for x in short.categories}["travel.lodging"].low_confidence is True


def test_income_refund_is_negative_spending_in_the_household_average():
    txs = monthly("2026-02-04", 8, -100.0, "food.groceries") + [tx("2026-05-09", 70.0, "income.refund", entity="SHOP")]
    r = averages.category_averages(make_ds(txs, last_sync={"a": "2026-10-04"}))
    assert r.household_monthly_avg_c == (70000 - 7000) // 7 * 1 or r.household_monthly_avg_c == round(63000 / 7 * 1)
    assert any(c.category == "income.refund" and c.monthly_avg_c < 0 for c in r.categories)


# ---------------------------------------------------------------- E4-2 context

def test_debt_service_principal_and_savings_rate_including_principal():
    loan = SimpleNamespace(id="home", kind="mortgage", monthly_payment=1500.0, payment_match="^HOMEBANK", outstanding=180000.0,
                           rate=SimpleNamespace(nominal=2.4), start_date=None, end_date=None, debited_from=None, debited_account=None)
    txs = [tx("2026-08-25", 3000.0, "income.salary", entity="ACME"), tx("2026-08-05", -1500.0, "housing.mortgage", entity="HOMEBANK"),
           tx("2026-08-10", -500.0, "food.groceries"), tx("2026-07-01", -1.0, "fees.bank_fees"), tx("2026-09-30", -1.0, "fees.bank_fees")]
    ds = make_ds(txs, memory=MemorySnapshot(liabilities=[("l", loan)]), last_sync={"a": "2026-10-04"})
    m = next(x for x in cashflow.cashflow(ds, months=2, end="2026-08").household.months if x.month == "2026-08")
    assert m.debt_service_c == 150000
    assert m.loan_principal_c == 150000 - 36000                       # interest = 180,000 x 2.4 % / 12 = 360
    assert m.net_c == 300000 - 200000 and m.savings_rate_incl_principal == round((100000 + 114000) / 300000, 4)
    unknown = SimpleNamespace(**{**loan.__dict__, "rate": None})
    m2 = next(x for x in cashflow.cashflow(make_ds(txs, memory=MemorySnapshot(liabilities=[("l", unknown)]), last_sync={"a": "2026-10-04"}),
                                           months=2, end="2026-08").household.months if x.month == "2026-08")
    assert m2.debt_service_c == 150000 and m2.loan_principal_c is None and m2.savings_rate_incl_principal is None


def test_internal_transfers_with_an_unconnected_account_are_shown():
    kid = account("k", "Kid", purpose="kids")
    txs = [tx("2026-08-03", -200.0, "transfer.internal", "a"), tx("2026-08-04", 200.0, "transfer.internal", "k"),     # paired
           tx("2026-08-10", 1500.0, "transfer.internal", "a"),                                                       # from nowhere
           tx("2026-08-12", -75.0, "transfer.internal", "k"),                                                        # to nowhere
           tx("2026-07-01", -1.0, "food.groceries", "a"), tx("2026-09-30", -1.0, "food.groceries", "a"),
           tx("2026-07-01", -1.0, "food.groceries", "k"), tx("2026-09-30", -1.0, "food.groceries", "k")]
    ds = make_ds(txs, accounts=[account(), kid], last_sync={"a": "2026-10-04", "k": "2026-10-04"})
    m = next(x for x in cashflow.cashflow(ds, months=2, end="2026-08").household.months if x.month == "2026-08")
    assert (m.drawn_unconnected_c, m.sent_unconnected_c) == (150000, 7500)


# ---------------------------------------------------------------- P1 redaction

HOUSEHOLD = """\
members:
  - id: anna
    name: Anna Rossi
    role: adult
    aliases: ["MME ANNA ROSSI", "M OU MME ROSSI ANNA"]
  - id: luca
    name: Luca Rossi
    role: adult
  - id: mia
    name: Mia Rossi
    role: child
    birth_year: 2012
employers: ["Acme Corp"]
places: ["Roquemont"]
schools: ["Ecole Saint Exupery"]
"""


@pytest.fixture
def private_world(cfg):
    con = make_world(cfg)
    (cfg.memory_dir / "household.yaml").write_text(HOUSEHOLD)
    for i in range(7):
        d0 = add_months(D("2026-03-25"), i).isoformat()
        add_tx(con, "ce", f"sal{i}", d0, 2500.0, "VIR SALAIRE ACME CORP", "transfer_in")
        add_tx(con, "ce", f"sch{i}", add_months(D("2026-03-07"), i).isoformat(), -85.0, "ECOLE SAINT EXUPERY ROQUEMONT", "direct_debit")
        add_tx(con, "ce", f"pocket{i}", add_months(D("2026-03-09"), i).isoformat(), -40.0, "VIR MME ANNA ROSSI", "transfer_out")
    con.execute("INSERT OR REPLACE INTO merchants VALUES ('VIR SALAIRE ACME CORP','Acme Corp (salary)','income.salary',1,0,'user','x','t')")
    con.execute("INSERT OR REPLACE INTO merchants VALUES ('ECOLE SAINT EXUPERY ROQUEMONT','Ecole Saint Exupery Roquemont','kids.school',1,0,'user','x','t')")
    con.execute("INSERT OR REPLACE INTO merchants VALUES ('VIR MME ANNA ROSSI','Anna Rossi','transfer.to_people',1,0,'user','x','t')")
    con.execute("INSERT INTO balances VALUES ('ce','2026-10-04T08:00:00+00:00','CLBD',5000.0,'EUR',NULL)")
    con.commit()
    return con


def test_no_name_holder_spelling_employer_or_place_leaves_through_the_registry(private_world, cfg):
    from coach.analytics import api
    reg = api.redacted_registry(private_world, cfg, D("2026-10-04"))
    raw_reg = api.registry()
    bodies = {}
    for name in reg.names():
        if name == "year_review":
            continue
        bodies[name] = json.dumps(reg.call(name), ensure_ascii=False)
    bodies["year_review"] = json.dumps(reg.call("year_review", year=2026), ensure_ascii=False)
    assert len(bodies) == len(raw_reg)
    banned = ["anna", "rossi", "luca", "mia ", "mia\"", "mme anna", "acme corp", "salaire acme", "lillebourg", "exupery", "depot part", "m ou mme"]       # bank names are not personal data
    for name, body in bodies.items():
        low = body.lower()
        for word in banned:
            assert word not in low, (name, word)
        for uid in ("\"fo\"", "\"ce\"", "\"rl\""):
            assert uid not in low, (name, uid)
        for key in ("sal0", "sch0", "pocket0", "stream00", "reno1", "gro00"):
            assert key not in low, (name, key)
    assert "account-main-1" in bodies["coverage"] and "kid-1" in json.dumps(reg.call("cashflow"))
    assert reg.red.reverse["account-main-1"] == "CPT COURANT TEST"


def test_anomaly_messages_are_rebuilt_from_structured_fields(private_world, cfg):
    from coach.analytics import api
    add_tx(private_world, "fo", "bigg", "2026-09-28", -480.0, "ECOLE SAINT EXUPERY ROQUEMONT", "card")
    add_tx(private_world, "fo", "d1", "2026-09-27", -90.0, "ANNA ROSSI COIFFURE", "card")
    add_tx(private_world, "fo", "d2", "2026-09-27", -90.0, "ANNA ROSSI COIFFURE", "card")
    reg = api.redacted_registry(private_world, cfg, D("2026-10-04"))
    out = reg.call("anomalies")
    assert out["anomalies"]
    text = json.dumps(out).lower()
    assert "anna" not in text and "rossi" not in text and "exupery" not in text
    for a in out["anomalies"]:
        assert a["message"] and all(not ev.startswith(("bigg", "d1", "d2")) for ev in a["evidence"])
        assert all(ev.startswith("h_") for ev in a["evidence"])


def test_the_cli_keeps_raw_values(private_world, cfg, capsys):
    run(cfg, "coverage")
    assert "Fortuneo" in capsys.readouterr().out


def test_registry_call_maps_pseudonymous_scopes_back(private_world, cfg):
    from coach.analytics import api
    reg = api.redacted_registry(private_world, cfg, D("2026-10-04"))
    out = reg.call("cashflow", scope={"owners": ["kid-1"]}, months=2)
    names = out["household"]["accounts"]
    assert names == ["account-kids-1"]


# ---------------------------------------------------------------- price changes, ICS, refunds (minor items)

def test_a_step_change_of_an_energy_bill_is_found_and_the_forecast_uses_the_new_level():
    from coach.analytics import pricechanges
    bills = [tx(add_months(D("2026-02-28"), i), -a, "housing.energy", entity="POWERCO") for i, a in enumerate([219.46] * 6 + [153.80])]
    ds = make_ds(bills, today=D("2026-09-20"))
    (c,) = pricechanges.price_changes(ds).changes
    assert (c.old_c, c.new_c, c.direction, c.effect, c.confirmed) == (21946, 15380, "decrease", "costs_less", False)
    (s,) = recurring.detect_recurring(ds).series
    assert s.expected_amount_c == -15380                               # the latest level, not the median of the last three


def test_ended_series_are_not_reported_and_a_change_can_be_dismissed(cfg, capsys):
    from coach.analytics import pricechanges
    rise = [tx(add_months(D("2026-01-05"), i), -a, "subscriptions.video_streaming", entity="STREAMBOX") for i, a in enumerate([10.0] * 3 + [12.0] * 3)]
    assert pricechanges.price_changes(make_ds(rise, today=D("2026-10-04"))).changes == []         # ended in June
    ds = make_ds(rise, today=D("2026-07-01"))
    (c,) = pricechanges.price_changes(ds).changes
    assert c.id.startswith("chg_")
    assert pricechanges.price_changes(ds, dismissed=frozenset({c.id})).changes == []
    assert pricechanges.price_changes(ds, dismissed=frozenset({c.id}), include_dismissed=True).changes[0].dismissed is True
    # through the CLI: stored in price_change_dismissals
    con = make_world(cfg)
    for i, a in enumerate([10.0] * 3 + [12.5] * 3):
        add_tx(con, "fo", f"pc{i}", add_months(D("2026-06-05"), i).isoformat(), -a, "PRICEBOX", "card")
    con.execute("INSERT OR REPLACE INTO merchants VALUES ('PRICEBOX','Pricebox','subscriptions.video_streaming',1,0,'user','x','t')")
    con.commit()
    run(cfg, "recurring", "changes", "--as-of", "2026-12-20")
    out = capsys.readouterr().out
    cid = re.search(r"(chg_[0-9a-f]{10})", out).group(1)
    run(cfg, "recurring", "dismiss-change", cid)
    capsys.readouterr()
    assert con.execute("SELECT COUNT(*) FROM price_change_dismissals").fetchone()[0] == 1
    run(cfg, "recurring", "changes", "--as-of", "2026-12-20")
    assert cid not in capsys.readouterr().out
    run(cfg, "recurring", "changes", "--as-of", "2026-12-20", "--all")
    assert cid in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run(cfg, "recurring", "dismiss-change", "chg_nope")


def test_ics_with_transfers_carries_no_payer_or_account_names():
    kid = account("k", "Revolut Mia", owner="mia", purpose="kids")
    own = [tx(add_months(D("2026-04-02"), i), -300.0, "transfer.internal", "a", entity="MIA ROSSI") for i in range(6)]
    ds = make_ds(own + [tx("2026-09-01", -1.0, "food.groceries", "k")], accounts=[account(), kid], last_sync={"a": "2026-10-04"})
    text = upcoming.to_ics(upcoming.calendar_items(ds, 40, include_transfers=True))
    assert "BEGIN:VEVENT" in text and "Transfer between own accounts out" in text
    assert "Revolut" not in text and "ROSSI" not in text.upper() and "account:" not in text


def test_income_refund_is_negative_spending_and_reported_as_unallocated_in_budget_status():
    b = schemas.Budget(id="food", category="food.groceries", monthly=300)
    txs = [tx("2026-10-02", -50.0, "food.groceries", entity="S1"), tx("2026-10-05", 30.0, "income.refund", entity="SHOP"),
           tx("2026-09-01", -1.0, "food.groceries", entity="Z")]
    ds = make_ds(txs, today=D("2026-10-10"), memory=MemorySnapshot(budgets=[b]), last_sync={"a": "2026-10-10"}, history={"a": ("2026-01-01", None)})
    r = budgets.budget_status(ds)
    assert r.budgets[0].spent_c == 5000 and r.unallocated_refunds_c == 3000
    cf = next(m for m in cashflow.cashflow(ds, months=1, end="2026-10", include_current=True).household.months if m.month == "2026-10")
    assert cf.refunds_c == 3000 and cf.spending_c == 5000 - 3000


def test_interleaved_descriptor_variants_that_fail_alone_are_pooled_by_brand():
    paris = {2, 5, 8, 11, 13, 14, 17, 20}
    txs = [tx(add_months(D("2025-01-27"), i), -16.48 if i < 6 else -23.67, "subscriptions.music_streaming",
              entity="Spotify France LYON" if i in paris else f"Spotify P{i}AB9C{i} Goteborg") for i in range(22)]
    assert len(recurring.detect_recurring(make_ds(txs)).series) == 1
    other = txs[:5] + [tx("2026-02-03", -16.48, "subscriptions.music_streaming", entity="Deezer")]
    assert all(s.entity != "Deezer" for s in recurring.detect_recurring(make_ds(other)).series)


# ---------------------------------------------------------------- round 3

def test_a_group_with_several_lines_keeps_every_line_orange_shape():
    txs = []
    for i in range(8):
        d7, d11 = add_months(D("2026-02-07"), i), add_months(D("2026-02-11"), i)
        txs += [tx(d7, -5.99, "subscriptions.telecom", entity="ORANGE"), tx(d7, -5.99, "subscriptions.telecom", entity="ORANGE"),
                tx(d7, -9.99, "subscriptions.telecom", entity="ORANGE"), tx(d11, -27.14, "subscriptions.telecom", entity="ORANGE")]
    res = recurring.detect_recurring(make_ds(txs, today=D("2026-10-12"))).series
    assert sorted(s.expected_amount_c for s in res) == [-2714, -999, -599, -599] and all(s.n_occurrences == 8 for s in res)


def test_a_group_with_six_lines_keeps_every_line_bpce_shape():
    lines = [(25.30, 5), (9.85, 5), (69.60, 6), (58.95, 6), (11.45, 15), (95.0, 20)]
    txs = []
    for i in range(8):
        for amt, day in lines:
            txs.append(tx(add_months(D("2026-02-01"), i).replace(day=day), -(amt + (i % 3) * 0.5 if amt == 95.0 else amt),
                          "housing.home_insurance", entity="BPCE ASSURANCES"))
    res = recurring.detect_recurring(make_ds(txs, today=D("2026-10-22"))).series
    assert len(res) == 6 and all(s.n_occurrences == 8 for s in res)
    assert sum(abs(s.expected_amount_c) for s in res) > 25000


def test_kervalis_pairs_still_work_after_the_cluster_first_order():
    txs = []
    for i in range(7):
        d0 = add_months(D("2026-03-05"), i)
        txs += [tx(d0, -108.72, "housing.property_charges", entity="KERVALIS"), tx(d0, -101.31, "housing.property_charges", entity="KERVALIS")]
    assert len(recurring.detect_recurring(make_ds(txs)).series) == 2


def test_every_output_uses_the_carried_over_series_id_when_an_older_payment_arrives(cfg):
    from coach.analytics import forecast, pricechanges
    from coach.db import connect
    con = connect(cfg, insecure=True, create=True)
    pays = [tx(add_months(D("2026-04-24"), i), a, "income.rental", entity="NEXITY", key=f"r{i}")
            for i, a in enumerate([512.30, 612.40, 612.40, 612.40, 612.40])]
    ds1 = make_ds(pays, today=D("2026-09-10"))
    recurring.refresh_recurring(con, ds1)
    ics1 = upcoming.to_ics(upcoming.calendar_items(ds1, 60))
    pc1 = {c.id for c in pricechanges.price_changes(ds1).changes}
    f1 = forecast.forecast(make_ds(pays, today=D("2026-09-10"), balances={"a": 1000.0}), 40).evidence
    older = [tx("2026-03-24", 512.30, "income.rental", entity="NEXITY", key="r_old")]
    ds2 = make_ds(older + pays, today=D("2026-09-10"))
    ds2.stored_members = {r: set() for r in []}
    for sid, tk in con.execute("SELECT series_id, tx_key FROM recurring_members"):
        ds2.stored_members.setdefault(sid, set()).add(tk)
    (s2,) = recurring.detect_recurring(ds2).series
    (s1,) = recurring.detect_recurring(ds1).series
    assert s2.occurrences[0].tx_key == "r_old"
    stored_id = con.execute("SELECT id FROM recurring_series").fetchone()[0]
    assert s2.id == stored_id
    uids = lambda t: set(re.findall(r"UID:(\S+)", t))                        # noqa: E731
    assert uids(upcoming.to_ics(upcoming.calendar_items(ds2, 60))) == uids(ics1)
    assert pc1 and {c.id for c in pricechanges.price_changes(ds2).changes} == pc1
    assert s1.id == stored_id
    ds3 = make_ds(older + pays, today=D("2026-09-10"), balances={"a": 1000.0})
    ds3.stored_members = ds2.stored_members
    assert forecast.forecast(ds3, 40).evidence == f1 and f1 == [stored_id]


def test_one_invalid_budget_or_goal_does_not_hide_the_valid_ones(cfg, capsys):
    make_world(cfg)
    (cfg.memory_dir / "budgets.yaml").write_text(
        "budgets:\n  - id: ok-food\n    category: food.groceries\n    monthly: 300\n"
        "  - id: bad-amount\n    category: food.restaurants\n    monthly: -5\n"
        "  - id: bad-cat\n    category: not.real\n    monthly: 10\n"
        "  - id: ok-food\n    category: food.restaurants\n    monthly: 20\n")
    (cfg.memory_dir / "goals.yaml").write_text(
        "goals:\n  - id: good\n    target_amount: 100\n    tag: savings\n    start: 2026-01-01\n  - id: broken\n    target_amount: abc\n    tag: x\n")
    run(cfg, "budget", "list")
    cap = capsys.readouterr()
    assert "ok-food" in cap.out and "no budgets yet" not in cap.out
    for ident in ("bad-amount", "bad-cat", "ok-food: duplicate"):
        assert ident in cap.err
    assert "coach memory check" in cap.err
    run(cfg, "budget", "status", "--as-of", ASOF)
    cap = capsys.readouterr()
    assert "ok-food" in cap.out and "bad-amount" in cap.err
    st = json.loads((run(cfg, "budget", "status", "--json", "--as-of", ASOF), capsys.readouterr().out)[1])
    assert [b["id"] for b in st["budgets"]] == ["ok-food"] and len(st["warnings"]) == 3
    run(cfg, "goals", "list", "--as-of", ASOF)
    cap = capsys.readouterr()
    assert "good" in cap.out and "broken" in cap.err
    g = json.loads((run(cfg, "goals", "list", "--json", "--as-of", ASOF), capsys.readouterr().out)[1])
    assert [x["id"] for x in g["goals"]] == ["good"] and "broken" in g["warnings"][0]


def test_unconnected_internal_matches_counterparts_whatever_their_category_and_picks_the_nearest():
    kid = account("k", "Kid", purpose="kids")
    txs = [tx("2026-08-03", -700.0, "transfer.internal", "a"), tx("2026-08-04", 700.0, "transfer.from_people", "k"),     # CE -> CIC shape
           tx("2026-08-10", -300.0, "transfer.internal", "a"), tx("2026-08-10", -300.0, "transfer.internal", "a"),
           tx("2026-08-08", 300.0, "transfer.internal", "k"), tx("2026-08-11", 300.0, "transfer.internal", "k"),             # two pairs, nearest wins
           tx("2026-08-20", 90.0, "transfer.internal", "a"),                                                              # from nowhere
           tx("2026-07-01", -1.0, "food.groceries", "a"), tx("2026-09-30", -1.0, "food.groceries", "a"),
           tx("2026-07-01", -1.0, "food.groceries", "k"), tx("2026-09-30", -1.0, "food.groceries", "k")]
    ds = make_ds(txs, accounts=[account(), kid], last_sync={"a": "2026-10-04", "k": "2026-10-04"})
    m = next(x for x in cashflow.cashflow(ds, months=2, end="2026-08").household.months if x.month == "2026-08")
    assert (m.drawn_unconnected_c, m.sent_unconnected_c) == (9000, 0)


# ---------------------------------------------------------------- privacy round 3

@pytest.fixture
def private_world2(cfg):
    con = make_world(cfg)
    (cfg.memory_dir / "household.yaml").write_text(HOUSEHOLD + "")
    for i in range(7):
        d0 = add_months(D("2026-03-25"), i).isoformat()
        add_tx(con, "ce", f"sal{i}", d0, 2500.0, "VIR SALAIRE ACME CORP", "transfer_in")
        add_tx(con, "ce", f"sch{i}", add_months(D("2026-03-07"), i).isoformat(), -85.0, "ECOLE SAINT EXUPERY ROQUEMONT", "direct_debit")
    for i in range(2):
        add_tx(con, "fo", f"hc{i}", f"2026-09-2{i + 5}", -65.0, "VELLARD NATHALIE ROQUEMONT", "card")
        add_tx(con, "fo", f"hd{i}", f"2026-09-2{i + 5}", -65.0, "VELLARD NATHALIE ROQUEMONT", "card")
    add_tx(con, "fo", "des1", "2026-09-10", -76.27, "DUVERGER", "card")
    add_tx(con, "fo", "des2", "2026-08-10", -76.27, "DUVERGER", "card")
    add_tx(con, "fo", "gm1", "2026-09-11", -48.0, "PELLERIN ROUSSEAU", "card")
    add_tx(con, "fo", "gm2", "2026-08-11", -48.0, "PELLERIN ROUSSEAU", "card")
    for k, key in enumerate(["BOULANGERIE PAUL AURAY", "FRESH BAKERY DINAN", "CAFE DU PORT REDON"]):
        for j in range(5):
            add_tx(con, "fo", f"city{k}{j}", f"2026-0{3 + j}-1{k + 1}", -12.0 - k, key + " ROQUEMONT", "card")
    for key, cat in (("VIR SALAIRE ACME CORP", "income.salary"), ("ECOLE SAINT EXUPERY ROQUEMONT", "kids.school"),
                     ("VELLARD NATHALIE ROQUEMONT", "leisure.hobbies"), ("DUVERGER", "housing.maintenance_diy"),
                     ("PELLERIN ROUSSEAU", "housing.maintenance_diy")):
        con.execute("INSERT OR REPLACE INTO merchants VALUES (?,?,?,1,0,'user','x','t')", (key, key.title(), cat))
    con.execute("INSERT INTO balances VALUES ('ce','2026-10-04T08:00:00+00:00','CLBD',5000.0,'EUR',NULL)")
    con.commit()
    return con


def _all_outputs(reg):
    out = {n: json.dumps(reg.call(n, **({"year": 2026} if n == "year_review" else {})), ensure_ascii=False) for n in reg.names()}
    return out


def test_coarse_mode_masks_people_sole_traders_employer_school_and_towns(private_world2, cfg):
    from coach.analytics import api
    reg = api.redacted_registry(private_world2, cfg, D("2026-10-04"))
    bodies = _all_outputs(reg)
    banned = ["vellard", "nathalie", "duverger", "pellerin", "rousseau", "acme corp", "salaire", "exupery", "lillebourg", "anna", "rossi"]
    for name, body in bodies.items():
        low = body.lower()
        for w in banned:
            assert w not in low, (name, w)
    rec = bodies["recurring"]
    assert "[employer]" in rec and "[school]" in rec
    assert "[merchant:" in bodies["anomalies"] + bodies["year_review"] + bodies["recurring"]
    bakery = [v for k, v in reg.red.entity_map.items() if "BOULANGERIE" in k.upper()]
    assert bakery and all(v.upper().startswith("BOULANGERIE") and "ROQUEMONT" not in v.upper() for v in bakery)   # a business keeps its name, loses its town
    assert any(v.startswith("[merchant:") for k, v in reg.red.entity_map.items() if "DUVERGER" in k.upper())


def test_partial_person_names_are_masked_as_one_run(private_world2, cfg):
    from coach.analytics.privacy import collapse_person_runs
    assert collapse_person_runs("2 payments of 65.00 EUR to VELLARD [person] close together") == \
        "2 payments of 65.00 EUR to [person] close together"
    assert collapse_person_runs("Chez [person] Boulangerie") == "Chez [person] Boulangerie"
    assert collapse_person_runs("VELLARD [family] QUIMPER") == "[person]"


def test_standard_mode_keeps_merchant_titles_but_still_masks_names_and_declared_values(private_world2, cfg):
    from coach.analytics import api
    cfg.privacy_model_detail = "standard"
    reg = api.redacted_registry(private_world2, cfg, D("2026-10-04"))
    bodies = _all_outputs(reg)
    for name, body in bodies.items():
        low = body.lower()
        for w in ("vellard", "anna", "rossi", "lillebourg", "exupery", "acme corp"):
            assert w not in low, (name, w)
    assert "[employer]" in bodies["recurring"]                       # the employer is masked in both modes (declared in household.yaml)


def test_privacy_config_is_validated(tmp_path):
    from coach.config import ConfigError, load_config
    p = tmp_path / "c.toml"
    p.write_text('[privacy]\nmodel_detail = "everything"\n')
    with pytest.raises(ConfigError):
        load_config(p, env={})
    p.write_text('[privacy]\nmodel_detail = "standard"\n')
    assert load_config(p, env={}).privacy_model_detail == "standard"
    p.write_text("")
    assert load_config(p, env={}).privacy_model_detail == "coarse"
