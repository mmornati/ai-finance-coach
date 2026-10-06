"""E15 memory side: the shipped taxonomy leaves and generic French rules, the schema of the property facts, `memory check` on the links, and the open
questions about the facts that are missing (never guessed). Synthetic data only."""
from __future__ import annotations


import pytest
import yaml

from coach.classify import rules as rules_mod, taxonomy
from coach.memory import check as check_mod, qgen, schemas as S
from coach.memory.store import MemoryStore
from memhelpers import TODAY, make_world, write
from rentalhelpers import RENTAL_ASSET, rental_world


# ---------------------------------------------------------------- the shipped categories and rules (E15-1)

def test_the_property_categories_exist_in_the_packaged_taxonomy():
    ids = {f"{g}.{leaf}" for g, ls in yaml.safe_load((rules_mod.HERE / "taxonomy.yaml").read_text()).items() for leaf in ls}
    assert {"income.rental", "housing.rental_property_loan", "housing.property_charges", "housing.property_tax", "housing.property_management",
            "housing.property_insurance", "housing.renovation"} <= ids
    from coach.rental import model as M
    assert set(M.CATEGORY_BUCKET) <= ids | {"taxes.taxes", "housing.home_insurance", "insurance.other", "housing.maintenance_diy"}


@pytest.mark.parametrize("key,direction,category", [
    ("PRLV DGFIP TAXE FONCIERE 2026", "out", "housing.property_tax"),
    ("PRELEVEMENT TAXE FONCIERE", "out", "housing.property_tax"),
    ("PRLV DGFIP IMPOT REVENU", "out", "taxes.taxes"),                         # the public-finance rule is still there for the rest
    ("FRAIS DE GESTION LOCATIVE", "out", "housing.property_management"),
    ("HONORAIRES DE GESTION LOYERS 04/2026", "out", "housing.property_management"),
    ("GERANCE LOCATIVE T2", "out", "housing.property_management"),
    ("COTISATION ASSURANCE PNO", "out", "housing.property_insurance"),
    ("ASSURANCE PROPRIETAIRE NON OCCUPANT", "out", "housing.property_insurance"),
    ("PRIME GLI", "out", "housing.property_insurance"),
    ("GARANTIE LOYERS IMPAYES", "out", "housing.property_insurance"),
    ("APPEL CHARGES DE COPRO T3", "out", "housing.property_charges"),
    ("SYNDIC DES TILLEULS", "out", "housing.property_charges"),
    ("VIR LOYER LOCATAIRE", "in", "income.rental"),
    ("VIR LOYERS SEPTEMBRE", "in", "income.rental"),
])
def test_generic_french_rules_label_the_property_flows(key, direction, category):
    assert rules_mod.rule_category(key, rules_mod.load_rules(), direction) == category


@pytest.mark.parametrize("key,direction", [
    ("FRAIS DE GESTION COMPTE TITRES", "out"), ("HONORAIRES DE GESTION PEA", "out"), ("FRAIS DE GESTION ASSURANCE VIE", "out"),
    ("FRAIS DE GESTION", "out"), ("REMBOURSEMENT LOYER TROP PERCU", "in"), ("RESTITUTION DEPOT DE GARANTIE LOYER", "in"),
    ("VIR CAUTION LOYER", "in"), ("REGULARISATION LOYER", "in")])
def test_bank_fees_and_loyer_refunds_or_deposits_are_not_property_flows(key, direction):
    got = rules_mod.rule_category(key, rules_mod.load_rules(), direction)
    assert got not in ("housing.property_management", "income.rental"), got


def test_what_the_household_pays_as_its_own_rent_is_not_labelled_as_rent_received():
    assert rules_mod.rule_category("VIR LOYER APPARTEMENT", rules_mod.load_rules(), "out") != "income.rental"


def test_the_new_rules_hold_only_generic_words_never_a_brand():
    rules = yaml.safe_load((rules_mod.HERE / "rules.yaml").read_text())["merchant_rules"]
    new = [m for m in rules if m["category"] in ("housing.property_tax", "housing.property_management", "housing.property_insurance")]
    assert len(new) == 4                                      # tax, management (out), management (refund in), insurance
    import re
    for m in new:
        words = set(re.findall(r"[A-Z]{3,}", m["match"].replace("\\s", " ")))
        assert words <= {"TAXE", "FONCI", "FONC", "ERE", "ERES", "GESTION", "LOCATIVE", "HONORAIRES", "FRAIS", "PNO", "PROPRIETAIRE", "NON", "OCCUPANT", "GLI",
                         "GARANTIE", "DES", "LOYERS", "LOYER", "IMPAYES", "IMPAYE", "DE", "GERANCE", "LOCATIVE", "IMMOBILIER", "DEPOT", "CAUTION", "REMBOURSEMENT", "REMBT", "RBT", "REGULARISATION", "AVOIR", "ANNULATION"}, words


def test_an_older_user_copy_gets_the_new_leaves_with_merge_package(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_CONFIG_DIR", str(tmp_path / "userconf"))
    try:
        user = tmp_path / "userconf"
        user.mkdir()
        pkg = (rules_mod.HERE / "taxonomy.yaml").read_text()
        ids = sorted(f"{g}.{leaf}" for g, ls in yaml.safe_load(pkg).items() for leaf in ls)
        new = ("housing.property_tax", "housing.property_management", "housing.property_insurance")
        old_ids = [i for i in ids if i not in new]
        text = "\n".join(l for l in pkg.splitlines() if not l.strip().startswith(("property_tax:", "property_management:", "property_insurance:")))
        (user / "taxonomy.yaml").write_text(f"{rules_mod.HASH_HEADER}deadbeefdeadbeef\n{rules_mod.IDS_HEADER}{','.join(old_ids)}\n{text}\n")
        rules_mod.reload_taxonomy()
        assert not set(new) & set(rules_mod.CATEGORIES)
        r = taxonomy.merge_package()
        assert set(r["leaves"]) == set(new) and set(new) <= set(rules_mod.CATEGORIES)
        assert taxonomy.merge_package()["leaves"] == []                                  # idempotent
    finally:
        monkeypatch.delenv("COACH_CONFIG_DIR")
        rules_mod.reload_taxonomy()


# ---------------------------------------------------------------- the schema of the facts

def test_the_commitment_block_validates_every_figure_the_owner_types():
    c = S.Commitment(start_date="2021-03-05", years=9, rent_cap_m2=14.5, surface_m2=40, tenant_income_limit=30000, reduction_rate_pct=18,
                     extension={"decision": "extend", "years": 3, "decided_on": "2029-06-01"})
    assert str(c.start_date) == "2021-03-05" and c.extension.decision == "extend"
    for bad in ({"years": 0}, {"years": 31}, {"reduction_rate_pct": 120}, {"rent_cap_m2": -1}, {"surface_m2": 0}, {"start_date": "2021-03-05T10:00:00"},
                {"start_date": "2022-01-01", "end_date": "2021-01-01"}, {"extension": {"decision": "maybe"}}, {"years": "nine"}):
        with pytest.raises(Exception):
            S.Commitment(**bad)
    with pytest.raises(Exception):
        S.MarketRate(rate_pct=-1)
    with pytest.raises(Exception):
        S.MarketRate(rate_pct=3, as_of="not-a-date")
    with pytest.raises(Exception):
        S.Vacancy(start="2026-02-01T00:00:00")


def test_an_older_asset_without_any_rental_field_still_loads_and_unknown_notes_are_kept():
    a = S.Asset(id="flat", kind="real_estate_rental", scheme="pinel", pinel_commitment_years=9, rent_monthly=600, my_own_note="x")
    assert a.commitment is None and a.vacancies == [] and a.market_rate is None and a.account is None and a.loan is None
    assert S.AssetsFile.model_validate({"assets": [{"id": "flat", "kind": "real_estate_rental", "vacancies": None}]}).assets[0].vacancies == []


def test_the_facts_round_trip_through_the_store_with_validation(cfg):
    con = rental_world(cfg)
    store = MemoryStore(cfg.memory_dir, history=True)
    a = next(x for x in store.assets() if x.id == "rental-flat-1")
    assert a.commitment.years == 9 and a.account == "Rentbank courant" and a.loan == "rental-loan" and a.vacancies[0].note == "works between two tenants"
    res = store.edit("assets.yaml", [{"op": "set", "path": "assets[rental-flat-1].commitment.extension", "value": {"decision": "not_extend"}}], source="test", dry_run=True)
    assert res.changed and "not_extend" in res.diff
    from coach.memory.store import MemoryStoreError, ValidationFailed
    with pytest.raises((MemoryStoreError, ValidationFailed)):
        store.edit("assets.yaml", [{"op": "set", "path": "assets[rental-flat-1].commitment.years", "value": 0}], source="test", dry_run=True)
    con.close()


# ---------------------------------------------------------------- memory check

def codes(cfg, con):
    store = MemoryStore(cfg.memory_dir, history=False)
    return {i.code: i for i in check_mod.run_check(store, con, today=TODAY)}


def test_a_consistent_property_raises_no_rental_issue(cfg):
    con = rental_world(cfg)
    got = codes(cfg, con)
    assert not [c for c in got if c.startswith("rental_")], got
    con.close()


def test_links_that_point_nowhere_are_reported(cfg):
    con = rental_world(cfg, asset=RENTAL_ASSET.replace("loan: rental-loan", "loan: ghost-loan").replace("account: Rentbank courant", "account: nope"))
    got = codes(cfg, con)
    assert "ghost-loan" in got["rental_unknown_loan"].message and got["rental_unknown_loan"].level == "warning"
    assert "'nope'" in got["rental_unknown_account"].message and got["rental_unknown_account"].level == "warning"
    con.close()


def test_an_account_that_is_not_flagged_rental_and_a_property_without_a_start_are_info(cfg):
    con = rental_world(cfg)
    con.execute("UPDATE accounts SET purpose='main' WHERE uid='rn'")
    con.commit()
    asset = RENTAL_ASSET.replace("      start_date: 2021-03-05\n", "")
    write(cfg.memory_dir / "assets.yaml", (cfg.memory_dir / "assets.yaml").read_text().replace(RENTAL_ASSET, asset))
    got = codes(cfg, con)
    assert got["rental_account_purpose"].level == "info" and "coach accounts set --purpose rental" in got["rental_account_purpose"].message
    assert got["rental_commitment_start_missing"].level == "info"
    con.close()


def test_a_rental_account_with_no_property_is_reported(cfg):
    con = make_world(cfg)
    from helpers import add_bank
    add_bank(con, "s9", "Rentbank", "FR", [("rn", "FR7600000000000000000099", "Rentbank courant")])
    con.execute("UPDATE accounts SET purpose='rental' WHERE uid='rn'")
    con.commit()
    got = codes(cfg, con)
    assert got["rental_account_without_property"].level == "info"
    con.close()


# ---------------------------------------------------------------- open questions (E15-3)

def questions(cfg, con):
    store = MemoryStore(cfg.memory_dir, history=False)
    res = qgen.generate(store, con, cfg, TODAY)
    return [q for q in res.new if (q.key or "").startswith("fill:rental:")], store


def test_a_complete_property_raises_no_question(cfg):
    con = rental_world(cfg)
    qs, _ = questions(cfg, con)
    assert qs == []
    con.close()


def test_missing_facts_become_one_open_question_per_property(cfg):
    asset = "\n  - id: rental-flat-1\n    kind: real_estate_rental\n    scheme: pinel\n    account: rn\n"
    con = rental_world(cfg, asset=asset, loan=False)
    qs, store = questions(cfg, con)
    (q,) = qs
    assert q.key == "fill:rental:rental-flat-1" and q.topic == "Rental property" and q.origin == "generated"
    assert q.evidence["missing"][0] == "loan" and "commitment.start_date" in q.evidence["missing"] and "value" in q.evidence["missing"]
    assert "the loan that financed it" in q.question and "the start of the scheme commitment" in q.question
    again = qgen.generate(store, con, cfg, TODAY)
    assert [x for x in again.new if x.key == q.key] == [q]                                    # nothing stored yet: generate is a pure proposal
    con.close()


def test_a_question_that_was_asked_is_never_asked_again_and_resolves_when_the_facts_arrive(cfg):
    from coach.memory import questions as Q
    asset = "\n  - id: rental-flat-1\n    kind: real_estate_rental\n    account: rn\n    loan: rental-loan\n"
    con = rental_world(cfg, asset=asset)
    qs, store = questions(cfg, con)
    (q,) = qs
    Q.add_many(MemoryStore(cfg.memory_dir, history=True), [q], source="test")
    store = MemoryStore(cfg.memory_dir, history=False)
    assert [x for x in qgen.generate(store, con, cfg, TODAY).new if x.key == q.key] == []
    stored = next(x for x in store.questions() if x.key == q.key)
    assert qgen.resolved_reason(stored, store, con, cfg) is None
    full = "\n  - id: rental-flat-1\n    kind: real_estate_rental\n    account: rn\n    loan: rental-loan\n    value: 100000\n    rent_monthly: 600\n    purchase_price: 90000\n    purchase_date: 2020-01-01\n"
    from memhelpers import ASSETS
    write(cfg.memory_dir / "assets.yaml", ASSETS.rstrip("\n") + "\n" + full)
    store = MemoryStore(cfg.memory_dir, history=False)
    assert "every fact asked about this rental property is now recorded" == qgen.resolved_reason(next(x for x in store.questions() if x.key == q.key), store, con, cfg)
    con.close()


def test_the_only_rental_account_counts_as_the_link_and_is_not_asked_about(cfg):
    asset = "\n  - id: rental-flat-1\n    kind: real_estate_rental\n    value: 1\n    rent_monthly: 1\n    purchase_price: 1\n    purchase_date: 2020-01-01\n"
    con = rental_world(cfg, asset=asset, loan=False)
    qs, _ = questions(cfg, con)
    assert [x.evidence["missing"] for x in qs] == [["loan"]]                                    # one rental account, one property without one: linked; only the loan is unknown
    con.close()
