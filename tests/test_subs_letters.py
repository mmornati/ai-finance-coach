"""E8-5: cancellation letters generated from templates (no model, no network, nothing sent). Snapshot tests on synthetic contracts:
the expected text is in tests/snapshots/letters/*.txt (regenerate with UPDATE_SNAPSHOTS=1 after a deliberate template change, then READ the
diff: the wording is legal-tone sensitive)."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from coach.memory import schemas
from coach.memory.store import MemoryStore
from coach.skills import cancel as C
from coach.subs import letters as L, service as S

TODAY = dt.date(2026, 10, 5)
SNAP = Path(__file__).parent / "snapshots" / "letters"
CONTACT = {"address": "12 rue de l'Exemple\n59000 Montfort-Test", "email": "jeanne.exemple@example.org"}
HOLDER = "Jeanne Exemple"


def D(s):
    return dt.date.fromisoformat(s)


def contract(**kw):
    base = dict(id="c1", provider="HomeSure Assurances", kind="insurance_home", start_date=D("2024-03-01"), renewal=D("2027-03-01"),
                contract_number="HS-0001")
    base.update(kw)
    return schemas.Contract(**base)


# name -> (contract, country, lang, channel, with holder + contact)
CASES = {
    "fr_lrar_insurance_hamon": (contract(), "FR", "fr", "lrar", True),
    "fr_lrar_insurance_first_year_non_renewal": (contract(id="c2", kind="insurance_car", provider="AutoSure", start_date=D("2026-03-01"),
                                                         renewal=D("2027-03-01"), contract_number="AS-42"), "FR", "fr", "lrar", True),
    "fr_email_telecom_in_commitment": (contract(id="c3", kind="telecom", provider="TelcoCo", start_date=D("2025-04-10"), renewal=None,
                                               commitment_end=D("2027-01-15"), notice_period_days=10, contract_number="TC-778899",
                                               billing={"amount": 29.99, "period": "monthly"}), "FR", "fr", "email", True),
    "fr_online_streaming": (contract(id="c4", kind="streaming", provider="StreamBox", start_date=D("2025-10-05"), renewal=None, contract_number=None,
                                     billing={"amount": 12.99, "period": "monthly"}), "FR", "fr", "online", True),
    "fr_lrar_energy": (contract(id="c5", kind="energy", provider="SunPower Energie", start_date=D("2023-01-10"), renewal=None, contract_number="SP-1"),
                       "FR", "fr", "lrar", True),
    "fr_lrar_nothing_known": (contract(id="c6", kind="other", provider=None, start_date=None, renewal=None, contract_number=None), "FR", "fr", "lrar", False),
    "it_lrar_telecom_bersani": (contract(id="c7", kind="telecom", provider="TelcoItalia Test", start_date=D("2024-06-01"), renewal=None,
                                         contract_number="TI-9"), "IT", "it", "lrar", True),
    "it_email_rcauto_non_renewal": (contract(id="c8", kind="insurance_car", provider="AutoItalia Test", start_date=D("2025-01-20"),
                                             renewal=D("2027-01-20"), contract_number="AI-77"), "IT", "it", "email", True),
    "en_lrar_home_insurance": (contract(), "FR", "en", "lrar", True),
    "en_online_energy_italy": (contract(id="c9", kind="energy", provider="LuceGas Test", start_date=D("2024-01-01"), renewal=None, contract_number="LG-3"),
                               "IT", "en", "online", True),
}


def render(name):
    c, country, lang, channel, full = CASES[name]
    res = C.cancellability_of(c, TODAY, country)
    return L.build(c, res, today=TODAY, lang=lang, channel=channel, holder_name=HOLDER if full else None, contact=CONTACT if full else None)


def snapshot_text(out) -> str:
    return (out["text"] + "\n=== to complete ===\n" + "\n".join(out["placeholders"]) + "\n=== notes ===\n" + "\n".join(out["notes"])
            + "\n=== legal basis ===\n" + "\n".join(f"{r['id']} | {r['source']} | reviewed {r['last_reviewed']}" for r in out["legal_basis"]) + "\n")


@pytest.mark.parametrize("name", sorted(CASES))
def test_letter_snapshot(name):
    got = snapshot_text(render(name))
    path = SNAP / f"{name}.txt"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAP.mkdir(parents=True, exist_ok=True)
        path.write_text(got, encoding="utf-8")
    assert path.exists(), f"missing snapshot {path.name}: run with UPDATE_SNAPSHOTS=1 and read it"
    assert got == path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- properties that must hold for every template

@pytest.mark.parametrize("name", sorted(CASES))
def test_every_letter_has_a_date_a_signature_line_a_contract_number_or_placeholder_and_a_verify_note(name):
    out = render(name)
    c, country, lang, channel, full = CASES[name]
    text = out["text"]
    assert out["sent"] is False and out["pdf"] is None and "no PDF writer" in out["pdf_note"]
    number = c.contract_number or L.PLACEHOLDER[lang]["number"]
    if channel != "online":
        assert number in text                                              # the number, or the visible placeholder
    if channel == "lrar":
        assert L.fmt_date(TODAY, lang) in text and L.T[lang]["signature"] in text         # date and signature line
    if not c.contract_number and channel != "online":
        assert "contract_number" in out["placeholders"]
    assert out["notes"][-1] == L.NOTES[lang]["verify"] and out["notes"][-1].startswith(("Brouillon", "Bozza", "Draft"))
    assert all(r["source"] and r["last_reviewed"] for r in out["legal_basis"])
    assert out["filename"].endswith(f"-{channel}-{lang}.txt")


def test_the_legal_basis_comes_from_the_rules_engine():
    ids = lambda n: [r["id"] for r in render(n)["legal_basis"]]
    assert ids("fr_lrar_insurance_hamon") == ["fr-hamon", "fr-3-clics"]
    assert ids("fr_lrar_insurance_first_year_non_renewal") == ["fr-chatel-insurance"]               # not yet free: the anniversary route
    assert ids("fr_email_telecom_in_commitment") == ["fr-telecom", "fr-3-clics"]
    assert ids("fr_lrar_energy") == ["fr-energy"]
    assert ids("it_lrar_telecom_bersani") == ["it-bersani-telecom"] and ids("it_email_rcauto_non_renewal") == ["it-rcauto"]
    # the statutory references in the French template are exactly those of the rules table
    out = render("fr_lrar_insurance_hamon")
    assert "L113-15-2" in out["text"] and "L215-1-1" in out["text"] and "2014-344" in out["text"]
    out = render("fr_lrar_insurance_first_year_non_renewal")
    assert "L113-12" in out["text"] and "ne pas le reconduire" in out["text"]


def test_when_to_send_and_what_leaving_costs_are_in_the_notes():
    n = render("fr_lrar_insurance_first_year_non_renewal")
    assert not any("Ne l'envoyez pas avant" in x for x in n["notes"])         # a non-renewal notice goes BEFORE the anniversary, not after
    assert n["send_by"] == D("2027-01-01") and any("Envoyez-la au plus tard le 1er janvier 2027" in x for x in n["notes"])   # 2 months before (L113-12)
    t = render("fr_email_telecom_in_commitment")
    # 103 days to 2027-01-15 = 4 months left; 25 % of 4 x 29.99 = 29.99
    assert any("environ 29.99 EUR (4 mois restants, 25 % de l'abonnement restant dû)" in x and "15 janvier 2027" in x for x in t["notes"])
    assert t["send_by"] is None                                             # no renewal date on file: no notice deadline to compute
    e = render("fr_lrar_energy")
    assert any("Ne résiliez pas avant" in x for x in e["notes"])             # never leave a gap in supply
    s = render("it_email_rcauto_non_renewal")
    assert s["send_by"] is None and "mancato rinnovo" in s["text"] and any("non si rinnova tacitamente" in x for x in s["notes"])
    first_year = contract(id="fy", start_date=D("2026-03-01"), renewal=None)
    out = L.build(first_year, C.cancellability_of(first_year, TODAY, "FR"), today=TODAY, lang="fr", channel="lrar", holder_name=HOLDER, contact=CONTACT)
    assert out["send_on_or_after"] == D("2027-03-01") and any("Ne l'envoyez pas avant le 1er mars 2027" in x for x in out["notes"])     # Hamon opens after a year


def test_a_deadline_already_past_is_called_out():
    # a contract of 2 years, renewing 2026-10-20 with 60 days' notice: the notice deadline was 2026-08-21
    c = contract(id="late", kind="software", start_date=D("2024-03-01"), renewal=D("2026-10-20"), notice_period_days=60)
    out = L.build(c, C.cancellability_of(c, TODAY, "FR"), today=TODAY, lang="en", channel="lrar", holder_name=HOLDER, contact=CONTACT)
    assert out["send_by"] == D("2026-08-21") and any("The notice deadline (21 August 2026) has passed" in n for n in out["notes"])
    c2 = contract(id="late2", kind="software", start_date=D("2024-03-01"), renewal=D("2026-12-20"), notice_period_days=30)
    out2 = L.build(c2, C.cancellability_of(c2, TODAY, "FR"), today=TODAY, lang="en", channel="lrar", holder_name=HOLDER, contact=CONTACT)
    assert out2["send_by"] == D("2026-11-20") and "Send it no later than 20 November 2026 (notice)." in out2["notes"]


def test_an_unknown_language_or_channel_is_refused():
    c = contract()
    res = C.cancellability_of(c, TODAY, "FR")
    with pytest.raises(ValueError, match="lang"):
        L.build(c, res, today=TODAY, lang="de")
    with pytest.raises(ValueError, match="channel"):
        L.build(c, res, today=TODAY, channel="fax")


def test_the_loan_insurance_letter_is_a_substitution_request_under_lemoine():
    c = contract(id="li", kind="other", provider="LoanSure", contract_number="LS-1")
    res = C.cancellability(C.Terms(kind="loan_insurance"), TODAY, "FR")
    out = L.build(c, res, today=TODAY, lang="fr", channel="lrar", holder_name=HOLDER, contact=CONTACT, family="loan_insurance")
    assert "substitution" in out["text"] and "L313-30" in out["text"] and "loi Lemoine" in out["text"]
    assert [r["id"] for r in out["legal_basis"]] == ["fr-lemoine"] and any("10 jours ouvrés" in n for n in out["notes"])


def test_no_letter_content_can_come_from_anywhere_but_the_contract_and_the_household():
    out = render("fr_lrar_insurance_hamon")
    assert "Jeanne Exemple" in out["text"] and "12 rue de l'Exemple" in out["text"] and "jeanne.exemple@example.org" in out["text"]
    assert "HomeSure Assurances" in out["text"]


# ---------------------------------------------------------------- the service: holder, contact, local rendering

def test_service_renders_from_the_memory_files(cfg):
    from memhelpers import make_world
    make_world(cfg).close()
    (cfg.memory_dir / "contracts").mkdir(exist_ok=True)
    (cfg.memory_dir / "contracts" / "homesure.yaml").write_text(
        "id: homesure\nprovider: HomeSure Assurances\nkind: insurance_home\nstart_date: 2024-03-01\nrenewal: 2027-03-01\ncontract_number: HS-0001\n"
        "billing: { amount: 22.5, period: monthly }\ndocuments: []\nnotes: ''\n")
    hh = (cfg.memory_dir / "household.yaml").read_text()
    (cfg.memory_dir / "household.yaml").write_text(hh + "country: FR\ncontact:\n  address: |-\n    12 rue de l'Exemple\n    59000 Montfort-Test\n  email: jeanne.exemple@example.org\n")
    store = MemoryStore(cfg.memory_dir, history=False)
    assert S.contact_of(store) == {"address": "12 rue de l'Exemple\n59000 Montfort-Test", "email": "jeanne.exemple@example.org"}
    assert S.holder_name(store) is None and S.holder_name(store, "luca") == "Luca Rossi"        # unknown holder: a placeholder, NOT the first adult
    assert S.holder_name(store, "joint") == "Anna Rossi & Luca Rossi"
    with pytest.raises(S.RefError):
        S.holder_name(store, "nobody")
    from coach.analytics import api as aapi
    from coach.db import connect
    con = connect(cfg, insecure=True)
    ds = aapi.build_dataset(con, cfg, TODAY)
    out = S.render_letter(store, ds, "homesure", channel="lrar", today=TODAY)
    assert out["lang"] == "fr" and "[titulaire du contrat]" in out["text"] and "Anna Rossi" not in out["text"]
    assert "59000 Montfort-Test" in out["text"] and "HS-0001" in out["text"] and "holder name" in " ".join(out["placeholders"])
    (cfg.memory_dir / "contracts" / "homesure.yaml").write_text((cfg.memory_dir / "contracts" / "homesure.yaml").read_text() + "holder: joint\n")
    ds = aapi.build_dataset(con, cfg, TODAY)
    assert "Anna Rossi & Luca Rossi" in S.render_letter(store, ds, "homesure", channel="lrar", today=TODAY)["text"]       # a joint contract: both names
    (cfg.memory_dir / "contracts" / "homesure.yaml").write_text((cfg.memory_dir / "contracts" / "homesure.yaml").read_text().replace("holder: joint", "holder: luca"))
    ds = aapi.build_dataset(con, cfg, TODAY)
    assert "Luca Rossi" in S.render_letter(store, ds, "homesure", channel="lrar", today=TODAY)["text"]
    en = S.render_letter(store, ds, "homesure", lang="en", channel="email", holder="luca", today=TODAY)
    assert "Luca Rossi" in en["text"] and en["lang"] == "en"
    with pytest.raises(S.RefError, match="draft one first"):
        S.render_letter(store, ds, "nope", today=TODAY)
    con.close()


def test_a_language_other_than_the_contracts_country_keeps_the_countrys_legal_basis_and_says_so():
    c = contract()
    res = C.cancellability_of(c, TODAY, "FR")
    it = L.build(c, res, today=TODAY, lang="it", channel="lrar", holder_name=HOLDER, contact=CONTACT)
    assert "L113-15-2" in it["text"] and "legge Hamon" not in it["text"] and [r["id"] for r in it["legal_basis"]] == ["fr-hamon", "fr-3-clics"]
    assert any("diritto FR" in n and "(fr)" in n for n in it["notes"])
    en = L.build(c, res, today=TODAY, lang="en", channel="lrar", holder_name=HOLDER, contact=CONTACT)
    assert not any("diritto" in n for n in en["notes"]) and [r["id"] for r in en["legal_basis"]] == ["fr-hamon", "fr-3-clics"]
    fr_it = L.build(contract(id="x", kind="telecom"), C.cancellability_of(contract(id="x", kind="telecom"), TODAY, "IT"), today=TODAY, lang="fr", channel="lrar")
    assert "D.L. 7/2007" in fr_it["text"] and any("droit IT" in n and "(it)" in n for n in fr_it["notes"])


def test_water_and_other_insurance_get_their_own_basis():
    water = contract(id="w", kind="water", provider="AquaCo", contract_number="W-1")
    ow = L.build(water, C.cancellability_of(water, TODAY, "FR"), today=TODAY, lang="fr", channel="lrar", holder_name=HOLDER, contact=CONTACT)
    assert "Code de l'énergie" not in ow["text"] and "fr-energy" not in [r["id"] for r in ow["legal_basis"]] and "fourniture d'eau" in ow["text"]
    pet = contract(id="p", kind="insurance_other", provider="PetSure", start_date=D("2024-03-01"), renewal=D("2027-03-01"), contract_number="P-1")
    res = C.cancellability_of(pet, TODAY, "FR")
    assert res["can_cancel_now"] is False and [r["id"] for r in res["rules"]] == ["fr-chatel-insurance", "fr-3-clics"]     # Code des assurances, not Hamon, not L215-1
    op = L.build(pet, res, today=TODAY, lang="fr", channel="lrar", holder_name=HOLDER, contact=CONTACT)
    assert "L113-12" in op["text"] and "L113-15-2" not in op["text"] and "L215-1 du Code de la consommation" not in op["text"]
    assert any("Hamon" in c for c in res["conditions"]) and res["anniversary_route"]["effective"] == D("2027-03-01")
    assert L.build(pet, C.cancellability_of(pet, TODAY, "IT"), today=TODAY, lang="it", channel="lrar")["legal_basis"][0]["id"] == "it-insurance"
