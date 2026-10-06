"""E8-1 / E8-2 / E8-3: the canonical inventory, the contract drafts, usage tracking and the cancellation info wired to the rules engine.
Synthetic data only (tests/subshelpers.py); every figure below is hand-computed from the invented payments."""
from __future__ import annotations

import datetime as dt

import pytest

from coach.analytics.upcoming import calendar_items
from coach.memory import schemas
from coach.memory.store import MemoryStore
from coach.skills import cancel as C
from coach.subs import draft as DR, reminders, service as S, usage as U
from subshelpers import TODAY, build_subs_world
from memhelpers import make_world


def D(s):
    return dt.date.fromisoformat(s)


@pytest.fixture
def world(cfg):
    con = build_subs_world(cfg)
    yield con
    con.close()


@pytest.fixture
def bundle(cfg, world):
    return S.load_bundle(world, cfg, TODAY, include_ended=True)


def row(b, name):
    return next(r for r in b.inv["rows"] if r["name"] == name)


# ---------------------------------------------------------------- E8-1 the inventory

def test_inventory_lists_every_recurring_cost_with_hand_checked_costs(bundle):
    inv = bundle.inv
    names = {r["name"]: r for r in inv["rows"]}
    # monthly x 12 (the series' yearly cost); the energy bill is variable: median of the last 6 payments = 85.00
    assert {n: (r["monthly"], r["yearly"]) for n, r in names.items() if r["status"] == "active"} == {
        "StreamBox": ("12.99", "155.88"), "TelcoCo": ("29.99", "359.88"), "Homesure Assurances": ("22.50", "270.00"),
        "Cloudbox": ("5.99", "71.88"), "Fitclub": ("39.90", "478.80"), "Sunpower Energie": ("85.00", "1020.00")}
    assert inv["totals"]["services"] == 6 and inv["totals"]["monthly"] == "196.37" and inv["totals"]["yearly"] == "2356.44"
    assert names["Oldapp"]["status"] == "ended" and names["Oldapp"]["next_charge"] is None
    # the loan is NOT a subscription
    assert not any("homebank" in r["name"].lower() for r in inv["rows"])


def test_groups_and_group_totals(bundle):
    g = bundle.inv["groups"]
    assert {k: (v["count"], v["monthly"], v["yearly"]) for k, v in g.items()} == {
        "energy_utilities": (1, "85.00", "1020.00"), "memberships": (1, "39.90", "478.80"), "telecom": (1, "29.99", "359.88"),
        "insurance": (1, "22.50", "270.00"), "streaming_media": (1, "12.99", "155.88"), "software_cloud": (1, "5.99", "71.88")}
    assert list(g) == ["energy_utilities", "memberships", "telecom", "insurance", "streaming_media", "software_cloud"]    # biggest yearly first
    assert row(bundle, "Cloudbox")["group"] == "software_cloud" and row(bundle, "Fitclub")["kind"] == "membership"


def test_contract_status_on_file_missing_and_expired(cfg, world):
    b = S.load_bundle(world, cfg, TODAY, include_ended=True)
    assert row(b, "StreamBox")["contract"]["status"] == "on_file" and row(b, "TelcoCo")["contract"]["id"] == "telco"
    assert row(b, "Fitclub")["contract"]["status"] == "missing" and row(b, "Fitclub")["draftable"] is True
    assert b.inv["totals"]["without_contract"] == 4 and b.inv["totals"]["expired_contracts"] == 0
    # every date on file is in the past -> expired (the dates may be stale)
    (cfg.memory_dir / "contracts" / "telco.yaml").write_text(
        (cfg.memory_dir / "contracts" / "telco.yaml").read_text().replace("commitment_end: 2027-01-15", "commitment_end: 2026-01-15"))
    b = S.load_bundle(world, cfg, TODAY, include_ended=True)
    t = row(b, "TelcoCo")["contract"]
    assert t["status"] == "expired" and t["expired_on"] == D("2026-01-15") and b.inv["totals"]["expired_contracts"] == 1


def test_price_history_and_changes_of_a_series(bundle):
    cb = row(bundle, "Cloudbox")
    assert [(h["from"], h["amount"]) for h in cb["price_history"]] == [(D("2026-01-06"), "4.99"), (D("2026-07-06"), "5.99")]
    assert [(p["old"], p["new"], p["direction"]) for p in cb["price_changes"]] == [("4.99", "5.99", "increase")]
    assert cb["next_charge"] == D("2026-10-06") and cb["first_seen"] == D("2026-01-06")
    assert row(bundle, "StreamBox")["price_history"] == [{"from": D("2025-10-05"), "amount": "12.99"}]


def test_a_contract_file_without_payments_in_the_bank_data_is_listed_with_its_billing_amount(cfg, world):
    (cfg.memory_dir / "contracts" / "gym-elsewhere.yaml").write_text(
        "id: gym-elsewhere\nprovider: Gym Elsewhere\nkind: membership\nmerchant_match: '^NOTHING PAID HERE'\nbilling: { amount: 20, period: quarterly }\n"
        "documents: []\nnotes: ''\n")
    b = S.load_bundle(world, cfg, TODAY)
    r = row(b, "Gym Elsewhere")
    assert r["ref"] == "contract:gym-elsewhere" and r["status"] == "contract_only" and r["cost_source"] == "contract"
    assert (r["monthly"], r["yearly"]) == ("6.67", "80.00")                      # 20 per quarter = 80 a year, 6.67 a month (half-even)
    assert r["group"] == "memberships" and b.inv["totals"]["services"] == 7


def test_ended_series_are_hidden_unless_asked(cfg, world):
    assert "Oldapp" not in {r["name"] for r in S.load_bundle(world, cfg, TODAY).inv["rows"]}
    assert "Oldapp" in {r["name"] for r in S.load_bundle(world, cfg, TODAY, include_ended=True).inv["rows"]}


def test_resolve_refs(bundle):
    inv = bundle.inv
    fit = row(bundle, "Fitclub")
    assert S.resolve(inv, fit["ref"])["name"] == "Fitclub" and S.resolve(inv, "telco")["name"] == "TelcoCo"
    assert S.resolve(inv, "fitc")["name"] == "Fitclub"                           # a unique name fragment
    with pytest.raises(S.RefError):
        S.resolve(inv, "fitc", allow_name=False)                                  # the model path never searches names
    with pytest.raises(S.RefError):
        S.resolve(inv, "nothing-like-this")


# ---------------------------------------------------------------- E8-1 bootstrap contract files

def test_drafts_infer_the_kind_from_the_category_group_and_fill_what_the_series_knows(bundle):
    drafts = {d.provider: d for d in DR.drafts(bundle.ds, bundle.rec)}
    assert set(drafts) == {"Sunpower Energie", "Fitclub", "Homesure Assurances", "Cloudbox"}
    assert DR.by_kind(list(drafts.values())) == {"energy": 1, "insurance_home": 1, "membership": 1, "software": 1}
    f = drafts["Fitclub"].value
    assert f == {"id": "fitclub", "provider": "Fitclub", "kind": "membership", "holder": None, "merchant_match": "^FITCLUB", "amount_match": None,
                 "start_date": D("2025-10-12"), "renewal": None, "billing": {"amount": 39.9, "period": "monthly"}, "commitment_end": None,
                 "notice_period_days": None, "cancellation": None, "usage": None, "keep": None, "contract_number": None, "documents": [],
                 "notes": "drafted from rec_243e021a61: dates and terms to fill"}
    assert drafts["Homesure Assurances"].value["merchant_match"] == r"^HOMESURE\s+ASSURANCES"
    assert drafts["Homesure Assurances"].value["start_date"] == D("2024-11-02")        # the first payment seen
    assert drafts["Cloudbox"].value["billing"] == {"amount": 5.99, "period": "monthly"}  # the LATEST price level
    assert drafts["Sunpower Energie"].missing == ["renewal", "commitment_end", "notice_period_days"]


def test_a_draft_validates_links_to_its_series_and_asks_about_the_missing_fields(cfg, world, bundle):
    store = MemoryStore(cfg.memory_dir, history=False)
    d = next(d for d in DR.drafts(bundle.ds, bundle.rec) if d.provider == "Fitclub")
    res = store.edit(d.rel, DR.ops_for(d), action="draft-contract", source="cli")
    assert res.changed and (cfg.memory_dir / "contracts" / "fitclub.yaml").exists()
    c = next(m for _r, m in store.contracts() if m.id == "fitclub")
    assert c.kind == "membership" and c.billing.amount == 39.9 and c.start_date == D("2025-10-12") and c.renewal is None
    b = S.load_bundle(world, cfg, TODAY)
    r = row(b, "Fitclub")
    assert r["contract"]["status"] == "on_file" and r["contract_id"] == "fitclub" and not r["draftable"]       # the regex really links
    assert b.inv["totals"]["without_contract"] == 3
    q = DR.question_for(d, TODAY)
    assert q.key == "fill:contract:fitclub" and "renewal, commitment_end, notice_period_days" in q.question and q.stake == 478.8


def test_drafted_contract_questions_never_duplicate_the_generators(cfg, world, bundle):
    from coach.memory import qgen
    store = MemoryStore(cfg.memory_dir, history=False)
    d = next(d for d in DR.drafts(bundle.ds, bundle.rec) if d.provider == "Fitclub")
    store.edit(d.rel, DR.ops_for(d), action="draft-contract", source="cli")
    keys = {q.key for q in qgen.null_field_questions(store, cfg, TODAY)}
    assert DR.question_for(d, TODAY).key in keys            # same key as the generic generator: `questions generate` skips it


def test_no_draft_for_loans_or_series_that_already_have_a_contract(bundle):
    assert not any(d.provider in ("StreamBox", "TelcoCo") or "Homebank" in d.provider for d in DR.drafts(bundle.ds, bundle.rec))


def test_clean_provider():
    assert DR.clean_provider("NETFLIX COM 123456") == "Netflix Com" and DR.clean_provider("Acme Gym") == "Acme Gym"
    assert DR.clean_provider("PRLV 8827364 SPOTIFY") == "Prlv Spotify"


# ---------------------------------------------------------------- E8-2 usage

def test_usage_model_accepts_a_mapping_or_a_legacy_text(tmp_path):
    c = schemas.Contract(id="a", usage={"frequency": "never", "last_used": "2026-06-01", "note": "n"})
    assert isinstance(c.usage, schemas.Usage) and c.usage.last_used == D("2026-06-01")
    assert U.usage_of(c) == {"frequency": "never", "last_used": D("2026-06-01"), "note": "n", "recorded": True}
    legacy = schemas.Contract(id="b", usage="we use it weekly")
    assert U.usage_of(legacy) == {"frequency": "unknown", "last_used": None, "note": "we use it weekly", "recorded": True}
    assert U.usage_of(schemas.Contract(id="c"))["recorded"] is False and U.usage_of(None)["recorded"] is False
    with pytest.raises(Exception):
        schemas.Contract(id="d", usage={"frequency": "sometimes"})                  # not one of daily / weekly / monthly / rarely / never / unknown
    with pytest.raises(ValueError):
        U.parse_frequency("often")


def test_a_recorded_never_with_continuing_payments_and_an_old_last_used_are_the_only_signals(bundle):
    sb = row(bundle, "StreamBox")["usage"]
    assert sb["recorded"] and sb["frequency"] == "never" and sb["last_used"] == D("2026-06-01")
    kinds = {s["kind"]: s for s in sb["signals"]}
    assert set(kinds) == {"unused_60_days", "paid_but_never_used"}
    assert kinds["unused_60_days"]["days"] == 125                                 # 2026-06-01 -> 2026-10-04
    assert kinds["paid_but_never_used"]["last_payment"] == D("2026-09-05")
    # nothing recorded -> nothing measurable, and the inventory says so (it never invents a signal)
    cb = row(bundle, "Cloudbox")["usage"]
    assert cb["signals"] == [] and cb["measurable"] is False and "not in the bank data" in cb["note_not_measurable"]
    assert bundle.inv["totals"]["usage_unknown"] == 2                              # Cloudbox, Fitclub (discretionary); telecom / insurance are not asked


def test_unused_means_the_USER_recorded_last_used_is_older_than_60_days():
    today = D("2026-10-04")
    rec = lambda lu: U.signals({"frequency": "weekly", "last_used": lu, "note": None, "recorded": True}, paying=True, today=today)
    assert rec(D("2026-08-05")) == []                                 # 60 days exactly: not "unused for 60 days"
    assert [s["kind"] for s in rec(D("2026-08-04"))] == ["unused_60_days"] and rec(D("2026-08-04"))[0]["days"] == 61
    assert rec(None) == []                                            # nothing recorded -> no reminder
    assert U.signals({"frequency": "never", "last_used": D("2026-01-01"), "note": None, "recorded": True}, paying=False, today=today) == []   # payments stopped
    # unknown payments (a contract the bank data does not show): the user's own date still counts
    assert [s["kind"] for s in U.signals({"frequency": "monthly", "last_used": D("2026-01-01"), "note": None, "recorded": True}, paying=None, today=today)] == ["unused_60_days"]


def test_usage_questions_are_generated_once_and_share_the_coachs_key(cfg, world):
    store = MemoryStore(cfg.memory_dir, history=False)
    b = S.load_bundle(world, cfg, TODAY)
    qs, skipped = U.usage_questions(world, cfg, store, b.rec, TODAY)
    assert {q.key.split(":")[0] for q in qs} == {"usage"} and len(qs) == 2 and skipped == 0       # Cloudbox, Fitclub (StreamBox has usage recorded)
    cb = row(b, "Cloudbox")
    assert f"usage:{cb['series_id']}" in {q.key for q in qs}
    from coach.memory import questions as Q
    Q.add_many(MemoryStore(cfg.memory_dir, history=False, source="cli"), qs, source="cli")
    qs2, skipped2 = U.usage_questions(world, cfg, store, b.rec, TODAY)
    assert qs2 == [] and skipped2 == 2                                               # no duplicates (open, answered or dismissed)
    assert row(S.load_bundle(world, cfg, TODAY), "Cloudbox")["usage"]["question_asked"] is True


def test_usage_ops_write_a_validated_mapping(cfg, world):
    store = MemoryStore(cfg.memory_dir, history=False, source="cli")
    store.edit("contracts/telco.yaml", S.usage_ops("rarely", D("2026-09-01"), "kids only"), action="usage", source="cli")
    c = next(m for _r, m in store.contracts() if m.id == "telco")
    assert c.usage.frequency == "rarely" and c.usage.last_used == D("2026-09-01") and c.usage.note == "kids only"
    assert "last_used: 2026-09-01" in (cfg.memory_dir / "contracts" / "telco.yaml").read_text()           # an unquoted YAML date


# ---------------------------------------------------------------- E8-3 cancellation info from the rules engine

def test_cancellation_info_of_a_telecom_inside_its_commitment_is_priced_by_the_rules(bundle):
    ci = row(bundle, "TelcoCo")["cancellation"]
    assert ci["can_cancel_now"] is True and ci["earliest_effective_date"] == D("2026-10-14") and ci["notice_period_days"] == 10
    ec = ci["early_termination_cost"]
    # 103 days to 2027-01-15 -> ceil(1030 / 305) = 4 months left; more than 12 months in -> 25 % of 4 x 29.99 = 29.99
    assert (ec["remaining_months"], ec["amount"], ec["free_exit_date"]) == (4, 29.99, D("2027-01-15")) and "25 %" in ec["basis"]
    assert [r["id"] for r in ci["legal_basis"]] == ["fr-telecom", "fr-3-clics"]
    assert all(r["source"] and r["last_reviewed"] == "2026-10" for r in ci["legal_basis"])
    assert "Verify with your contract" in ci["verify"] and "not legal advice" in ci["disclaimer"] and ci["method"]


def test_cancellation_info_without_a_contract_uses_the_first_payment_as_a_lower_bound(bundle):
    ci = row(bundle, "Homesure Assurances")["cancellation"]
    assert ci["family"] == "insurance_home" and ci["can_cancel_now"] is True and ci["earliest_effective_date"] == D("2026-11-04")
    assert [r["id"] for r in ci["legal_basis"]][0] == "fr-hamon"            # first payment 2024-11: over a year -> Hamon applies
    assert row(bundle, "Cloudbox")["cancellation"]["family"] == "subscription"
    t = bundle.inv["totals"]
    assert t["with_cancellation_rule"] == 6 and t["cancellation_decidable"] >= 4


def test_the_country_of_the_household_selects_the_italian_rules(cfg, world):
    (cfg.memory_dir / "household.yaml").write_text("members: []\ncountry: IT\n")
    b = S.load_bundle(world, cfg, TODAY)
    assert b.inv["country"] == "IT"
    ci = row(b, "TelcoCo")["cancellation"]
    assert ci["country"] == "IT" and [r["id"] for r in ci["legal_basis"]] == ["it-bersani-telecom"] and ci["can_cancel_now"] is False
    assert ci["earliest_effective_date"] == D("2027-01-15")                   # inside the commitment: not before it ends


def test_notice_deadlines_come_from_the_rules_engine():
    today = D("2026-10-04")
    # the contract's own notice: 60 days before the renewal
    c = schemas.Contract(id="ins", provider="HomeSure", kind="insurance_home", start_date=D("2024-03-01"), renewal=D("2027-01-10"), notice_period_days=60)
    res = C.cancellability_of(c, today, "FR")
    assert [(d["kind"], d["date"]) for d in C.notice_deadlines(c, res, today)] == [("contract_notice", D("2026-11-11"))]
    # no notice on file, a contract in its first year: the legal anniversary deadline (2 months, L113-12) and the day free cancellation opens
    c2 = schemas.Contract(id="ins2", provider="HomeSure", kind="insurance_home", start_date=D("2026-03-01"), renewal=D("2027-03-01"))
    res2 = C.cancellability_of(c2, today, "FR")
    dls = {d["kind"]: d for d in C.notice_deadlines(c2, res2, today)}
    assert dls["legal_notice"]["date"] == D("2027-01-01") and dls["window_opens"]["date"] == D("2027-03-01")
    assert dls["legal_notice"]["rules"] == ["Tacit renewal of insurance (Loi Chatel)"]
    assert C.notice_deadlines(c2, res2, today, horizon=D("2026-12-31")) == []         # beyond the horizon


def test_the_calendar_uses_the_rules_engine_for_notice_deadlines(cfg, world):
    (cfg.memory_dir / "contracts" / "homeins.yaml").write_text(
        "id: homeins\nprovider: HomeSure\nkind: insurance_home\nstart_date: 2026-03-01\nrenewal: 2027-03-01\nbilling: { amount: 22.5, period: monthly }\n"
        "documents: []\nnotes: ''\n")
    b = S.load_bundle(world, cfg, TODAY)
    res = calendar_items(b.ds, 200, recurring=b.rec)
    items = {(i.kind, i.ref): i for i in res.items if i.source == "contract"}
    nd = items[("notice_deadline", "homeins")]
    assert nd.date == D("2027-01-01") and "anniversary" in nd.title and "Loi Chatel" in nd.note                 # 2 months before 2027-03-01
    assert items[("cancel_window_opens", "homeins")].date == D("2027-03-01")
    # the telecom commitment notice is the contract's own: 10 days before 2027-01-15, inside a 200-day window
    tel = items[("notice_deadline", "telco")]
    assert tel.date == D("2027-01-05") and tel.note == "10 days before 2027-01-15"


def test_calendar_and_insights_carry_the_usage_reminders(cfg, world):
    b = S.load_bundle(world, cfg, TODAY)
    cal = calendar_items(b.ds, 14, recurring=b.rec)
    unused = [i for i in cal.items if i.kind in ("unused_reminder", "never_used_reminder")]
    assert {i.kind for i in unused} == {"unused_reminder", "never_used_reminder"}
    assert len(unused) == 2 and all(i.title.startswith("StreamBox:") and "your own record" in i.note for i in unused)
    assert {i.title.split(": ")[1] for i in unused} == {"unused for 125 days (last used 2026-06-01, as you recorded)", "you recorded 'never used' and it is still being paid"}
    assert any("unused for 125 days" in i.title for i in unused)
    cards = reminders.subscription_cards(b.ds, b.rec)
    assert {c["subtype"] for c in cards} >= {"unused"} and all(c["kind"] == "subscription" for c in cards)
    assert any("unused for 125 days" in c["title"] and c["amount"] == "155.88" for c in cards)
    assert len({c["id"] for c in cards}) == len(cards)                            # stable, distinct ids (dismissals persist)


def test_nothing_is_reminded_for_a_service_whose_use_leaves_no_trace(cfg, world):
    """A software subscription with no recorded usage gets no 'unused' signal: usage is unknown, never inferred."""
    b = S.load_bundle(world, cfg, TODAY)
    assert not [c for c in reminders.subscription_cards(b.ds, b.rec) if "Cloudbox" in c["title"] or "Fitclub" in c["title"]]
    assert not [i for i in calendar_items(b.ds, 30, recurring=b.rec).items if i.kind in ("unused_reminder", "never_used_reminder") and "Cloudbox" in i.title]


def test_the_subscription_audit_understands_the_structured_usage(bundle):
    from coach.skills import subaudit as SA
    out = SA.subscription_audit(bundle.ds, bundle.rec)
    items = {i["entity"]: i for i in out["items"]}
    assert items["Streambox"]["usage"] == "recorded" and items["Cloudbox"]["usage"] == "unknown"
    assert {n["series"] for n in out["usage_questions_needed"]} >= {items["Cloudbox"]["series"]}
    assert items["Streambox"]["series"] not in {n["series"] for n in out["usage_questions_needed"]}


def test_kind_inference_by_category_group():
    from coach.subs.inventory import kind_of_category as k
    assert [k(c) for c in ("housing.home_insurance", "transport.car_insurance", "health.health_insurance", "housing.energy", "housing.water",
                           "subscriptions.telecom", "subscriptions.video_streaming", "subscriptions.music_streaming", "subscriptions.software_cloud",
                           "subscriptions.memberships", "leisure.sports_activities")] == [
        "insurance_home", "insurance_car", "insurance_health", "energy", "water", "telecom", "streaming", "streaming", "software", "membership", "membership"]
    # a life / pet / legal-protection insurance is NOT given the home-insurance rules (Hamon): "insurance_other" (Code des assurances anniversary rule, no Hamon)
    assert k("insurance.life") == "insurance_other" and k("insurance.pet") == "insurance_other" and k("insurance.other") == "insurance_other"
    assert k("subscriptions.other") == "other"


def test_a_contract_drafted_without_a_bank_label_match_is_not_counted_twice(cfg, world):
    """A draft whose merchant_match could not be built still belongs to its series (its notes say where it came from): one row, not two."""
    b = S.load_bundle(world, cfg, TODAY)
    fit = row(b, "Fitclub")
    (cfg.memory_dir / "contracts" / "fitclub.yaml").write_text(
        f"id: fitclub\nprovider: Fitclub\nkind: membership\nstart_date: 2025-10-12\nbilling: {{ amount: 39.9, period: monthly }}\n"
        f"documents: []\nnotes: 'drafted from {fit['ref']}: dates and terms to fill'\n")
    b2 = S.load_bundle(world, cfg, TODAY)
    fits = [r for r in b2.inv["rows"] if r["name"] == "Fitclub"]
    assert len(fits) == 1 and fits[0]["contract"]["status"] == "on_file" and fits[0]["ref"] == fit["ref"] and fits[0]["linked_series"] == [fit["ref"]]
    assert b2.inv["totals"]["services"] == 6 and b2.inv["totals"]["yearly"] == "2356.44"          # unchanged: no double count
    assert fit["ref"] not in {d.series_id for d in DR.drafts(b2.ds, b2.rec)}                          # and it is not drafted again


def test_the_draft_regex_uses_the_longest_prefix_that_matches_and_never_a_generic_word():
    from types import SimpleNamespace
    from anhelpers import tx
    from coach.analytics.recurring import Occurrence

    def series_of(mkeys, entity="X"):
        txs = [tx(f"2026-0{i + 1}-15", -14.0, "subscriptions.memberships", entity=entity, mkey=mk, key=f"k{i}") for i, mk in enumerate(mkeys)]
        ds = SimpleNamespace(txs=txs)
        x = SimpleNamespace(occurrences=[Occurrence(t.key, t.date, t.amount_c) for t in txs], entity=entity)
        return ds, x
    # the same three words every time: the whole label (up to four words)
    assert DR.match_regex(*series_of(["VARYCO MONTHLY FEE"] * 5)) == r"^VARYCO\s+MONTHLY\s+FEE"
    # the words after the first vary (a reference, a town): the longest prefix that still matches at least half of the payments
    assert DR.match_regex(*series_of([f"VARYCO REF{i}X TOWN{i}" for i in range(6)])) == "^VARYCO"
    assert DR.match_regex(*series_of(["VARYCO A FEE", "VARYCO A FEE", "VARYCO A FEE", "VARYCO B FEE"])) == r"^VARYCO\s+A\s+FEE"
    # a generic payment word alone is never a match (it would link every merchant that starts with it)
    assert DR.match_regex(*series_of([f"PRLV {w}" for w in ("AAA", "BBB", "CCC", "DDD")], entity="Z")) is None
    assert DR.match_regex(*series_of([], entity="X")) is None
    assert {"PRLV", "VIR", "SEPA"} <= DR.GENERIC_TOKENS


def test_series_that_share_a_bank_label_get_one_contract_each_told_apart_by_an_amount_band(cfg, world):
    """Two amounts of one merchant are two series: one contract file each, with an amount_match band so that neither links to both."""
    from helpers import add_tx
    for i in range(12):                                   # a second FITCLUB amount on another day: a second series of the same label
        m = (10 + i - 1) % 12 + 1
        add_tx(world, "fo", f"fitb{i:02d}", f"{2025 + (10 + i - 1) // 12}-{m:02d}-20", -19.90, "FITCLUB", "direct_debit")
    world.commit()
    b = S.load_bundle(world, cfg, TODAY)
    drafts = sorted((d for d in DR.drafts(b.ds, b.rec) if d.provider == "Fitclub"), key=lambda d: d.value["billing"]["amount"])
    assert [d.value["billing"]["amount"] for d in drafts] == [19.9, 39.9] and [d.contract_id for d in drafts] == ["fitclub-2", "fitclub"]
    assert all(d.shared_label and d.value["amount_match"]["tolerance_pct"] >= 3 and not d.warnings for d in drafts)
    assert {d.value["amount_match"]["amount"] for d in drafts} == {19.9, 39.9}
    assert "share this bank label: what does this contract cover" in DR.question_for(drafts[0], TODAY).question
    store = MemoryStore(cfg.memory_dir, history=False, source="cli")
    for d in drafts:
        store.edit(d.rel, DR.ops_for(d), action="draft-contract", source="cli")
    b2 = S.load_bundle(world, cfg, TODAY)
    fit2 = {r["monthly"]: r for r in b2.inv["rows"] if r["name"] == "Fitclub"}
    assert fit2["19.90"]["contract_id"] != fit2["39.90"]["contract_id"] and len(fit2["19.90"]["linked_series"]) == 1     # each links to ITS series only
    assert b2.inv["totals"]["without_contract"] == 3 and DR.link_report(b2.ds, b2.rec, []) == []
    assert not [x for x in DR.candidates(b2.rec, b2.ds) if x.entity == "Fitclub"]


def test_a_label_that_starts_another_series_label_is_made_specific_and_every_series_matches_exactly_one_contract(cfg, world):
    """'orange' (^ORANGE\\s+SA) would match the payments of 'orange-2' (ORANGE SA PARIS): the draft gets an amount band, and a global
    post-check confirms every series is matched by exactly one contract."""
    from helpers import add_tx
    from memhelpers import label, monthly
    monthly(world, "fo", "or1", "ORANGE SA", [-20.0] * 8, tx_type="direct_debit", start=(2026, 2), day=3)
    monthly(world, "fo", "or2", "ORANGE SA PARIS", [-35.0] * 8, tx_type="direct_debit", start=(2026, 2), day=17)
    label(world, "ORANGE SA", "subscriptions.telecom")
    label(world, "ORANGE SA PARIS", "subscriptions.telecom")
    world.commit()
    b = S.load_bundle(world, cfg, TODAY)
    ds = [d for d in DR.drafts(b.ds, b.rec) if d.provider.startswith("Orange")]
    assert len(ds) == 2
    by = {d.value["billing"]["amount"]: d for d in ds}
    short, long_ = by[20.0], by[35.0]
    assert short.value["merchant_match"] == r"^ORANGE\s+SA" and short.value["amount_match"] == {"amount": 20.0, "tolerance_pct": 3.0} and short.shared_label
    assert long_.value["merchant_match"] == r"^ORANGE\s+SA\s+PARIS" and long_.value["amount_match"] is None and not long_.shared_label
    store = MemoryStore(cfg.memory_dir, history=False, source="cli")
    for d in ds:
        store.edit(d.rel, DR.ops_for(d), action="draft-contract", source="cli")
    b2 = S.load_bundle(world, cfg, TODAY)
    rows = [r for r in b2.inv["rows"] if r["name"].startswith("Orange")]
    assert len(rows) == 2 and len({r["contract_id"] for r in rows}) == 2 and all(len(r["linked_series"]) == 1 for r in rows)
    orange = {x.id for x in b2.rec.series if x.entity.startswith("Orange")}
    assert all(len([l for l in x.links if l.kind == "contract"]) == 1 for x in b2.rec.series if x.id in orange)      # exactly one contract per series


def test_amount_match_in_the_contract_schema_restricts_the_link(cfg, world):
    (cfg.memory_dir / "contracts" / "fit-small.yaml").write_text("id: fit-small\nprovider: F\nmerchant_match: '^FITCLUB'\namount_match: { amount: 39.9 }\ndocuments: []\nnotes: ''\n")
    b = S.load_bundle(world, cfg, TODAY)
    assert row(b, "F")["contract_id"] == "fit-small"
    (cfg.memory_dir / "contracts" / "fit-small.yaml").write_text("id: fit-small\nprovider: F\nmerchant_match: '^FITCLUB'\namount_match: { amount: 10, tolerance_pct: 5 }\ndocuments: []\nnotes: ''\n")
    b = S.load_bundle(world, cfg, TODAY)
    assert row(b, "Fitclub")["contract"]["status"] == "missing" and row(b, "F")["status"] == "contract_only"      # 39.90 is outside 10 +- 5 %
