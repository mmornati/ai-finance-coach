"""E12-1: the fixture synthesizer (no real token may come out) and the parser tests over the committed, invented, real-shaped fixtures.

The "real" database of the synthesizer tests is itself invented: its merchants, people, towns and numbers are SENTINELS (words that exist
nowhere else), and the tests grep the output for them."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import pytest

from coach.classify.normalize import normalize_all
from coach.classify.parsers import parse_tx
from coach.classify.parsers.common import Household, RawTx
from coach.cli import main
from coach.config import load_config
from coach.db import connect
from coach.ingest.fingerprint import is_position_ref
from coach.ingest.sync import sync_account
from coach.quality import fixtures as F
from helpers import FakeClient, add_bank

FIXTURES = Path(__file__).parent / "fixtures"
BANK_FILES = {"fortuneo": "Fortuneo", "caisse_epargne": "Caisse d'Epargne Test", "cic": "CIC", "revolut": "Revolut"}

# words that exist only in this file: stand-ins for the merchants, people and towns of a real database
SENTINELS = ["QUORBEX", "ZANTHOR", "KARMOVEX", "YOMBRELA", "SELVARIN", "TRUMBLEFORD", "VAXILLOR", "OMBRAXIA"]
SENTINEL_DIGITS = ["518229445410506", "123456789012", "98765432101", "7630001007941234567890185"]


def eb(ref, date, amount, desc, creditor="", debit=True, code=None):
    tx = {"entry_reference": ref, "booking_date": date, "value_date": date, "status": "BOOK",
          "transaction_amount": {"amount": f"{abs(amount):.2f}", "currency": "EUR"},
          "credit_debit_indicator": "DBIT" if debit else "CRDT", "creditor": {"name": creditor}, "debtor": {"name": None},
          "remittance_information": desc if isinstance(desc, list) else [desc]}
    if code:
        tx["bank_transaction_code"] = {"code": code, "description": None, "sub_code": None}
    return tx


def real_world(cfg):
    """An invented 'real' database: three banks, sentinel words, an IBAN and long numbers inside the descriptors."""
    con = connect(cfg, insecure=True, create=True)
    add_bank(con, "s1", "Fortuneo", "FR", [("fo", "FR7600000000000000000001", "M OU MME SELVARIN NICOLAS")])
    add_bank(con, "s2", "Caisse d'Epargne Test", "FR", [("ce", "FR7600000000000000000002", "CPT COURANT TEST")])
    add_bank(con, "s3", "Revolut", "LT", [("rl", "LT0000000000000000001", "Nicolas Selvarin")])
    fo = []
    for i in range(30):
        d = f"2026-03-{(i % 27) + 1:02d}"
        fo.append(eb(f"{d}T00:00:00-{i // 27}", d, -(5 + i % 7) - 0.5, f"CARTE {(i % 27) + 1:02d}/03 QUORBEX NANTERRE" if i % 2 else f"CARTE {(i % 27) + 1:02d}/03 VAXILLOR TRUMBLEFORD"))
    fo += [eb("2026-04-01T00:00:00-0", "2026-04-01", -87.1, "CARTE 30/03 OMBRAXIA TRUMBLEFORD 95,00 USD COURS 1,09"),
           eb("2026-04-02T00:00:00-0", "2026-04-02", -25.0, "VIR INST WERO Jean MARTIN"),
           eb("2026-04-03T00:00:00-0", "2026-04-03", -60.0, "RET DAB 12345 QUORBEX TRUMBLEFORD"),
           eb("2026-04-04T00:00:00-0", "2026-04-04", 30.0, "ANN CARTE QUORBEX NANTERRE", debit=False),
           eb("2026-04-05T00:00:00-0", "2026-04-05", 2500.0, "VIR MR OU MME SELVARIN NICOLAS  VIREMENT DE MR OU MME SELVARIN NICOLAS  2518229445410506", debit=False)]
    ce = [eb(f"2026{i:02d}0100000000000000000000000{i:02d}", f"2026-{i:02d}-05", -42.0 - i, f"PRLV SEPA ZANTHOR ENERGIE REF 123456789012 MANDAT ABC{i:06d} FR7630001007941234567890185")
          for i in range(1, 9)]
    ce += [eb("20260301000000000000000000000099", "2026-03-06", -812.4, "ECH PRET 98765432101 DU 05/03/26"),
           eb("20260302000000000000000000000098", "2026-03-07", 600.0, "VIR SEPA RECU /DE KARMOVEX SARL /MOTIF LOYER MARS", debit=False)]
    rl = [eb("a1b2c3d4-e5f6-7890-abcd-ef1234567890", "2026-03-08", -12.9, "Vaxillor Trumbleford", code="CARD_PAYMENT"),
          eb("b1b2c3d4-e5f6-7890-abcd-ef1234567891", "2026-03-09", 20.0, "To Nicolas Selvarin", code="TRANSFER", debit=False)]
    for uid, items in (("fo", fo), ("ce", ce), ("rl", rl)):
        fc = FakeClient({uid: [{"transactions": items}]})
        sync_account(con, fc, uid, False, True, out=lambda *_: None)
    normalize_all(con, cfg.memory_dir)
    return con


@pytest.fixture
def real(cfg):
    con = real_world(cfg)
    yield con
    con.close()


def synth(con, cfg, bank="fortuneo", n=60, seed=12):
    return F.synthesize(con, cfg, bank, n, seed)


# ---------------------------------------------------------------- the synthesizer: nothing real comes out

@pytest.mark.parametrize("bank", ["fortuneo", "caisse_epargne", "revolut"])
def test_no_real_word_or_number_is_written(cfg, real, bank):
    fx, rep = synth(real, cfg, bank)
    text = json.dumps(fx).upper()
    for s in SENTINELS + ["NICOLAS", "JEAN", "MARTIN", "NANTERRE"]:
        assert s not in text, s
    for d in SENTINEL_DIGITS:
        assert d not in text, d
    assert rep["leaks"] == 0 and rep["kept"] == len(fx["transactions"]) and rep["terms_checked"] > 20


def test_the_household_is_invented_too(cfg, real):
    fx, _ = synth(real, cfg, "fortuneo")
    toks = {t for h in fx["household"]["holders"] for t in h} | set(fx["household"]["family"]) | set(fx["household"]["first_names"])
    assert toks and not toks & {"SELVARIN", "NICOLAS"}
    assert fx["account"]["name"].startswith("M OU MME ") and "SELVARIN" not in fx["account"]["name"]


def test_every_iban_like_string_is_a_test_iban_that_no_validator_accepts(cfg, real):
    fx, _ = synth(real, cfg, "caisse_epargne")
    text = json.dumps(fx)
    assert not F.iban_valid(fx["account"]["iban"]) and fx["account"]["iban"][2:4] == "00"
    found = re.findall(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b", text)
    assert found                                                    # the shape (an IBAN inside a direct debit line) is kept ...
    assert all(not F.iban_valid(x) and x[2:4] == "00" for x in found)       # ... but it is never a valid one
    assert "7630001007941234567890185" not in text


def test_the_known_valid_iban_is_recognised_as_valid_by_the_validator():
    assert F.iban_valid("FR7630006000011234567890189") and F.iban_valid("GB82 WEST 1234 5698 7654 32")
    assert not F.iban_valid("FR0030006000011234567890189")


def test_the_shape_survives_lengths_prefixes_dates_and_padding(cfg, real):
    fx, _ = synth(real, cfg, "fortuneo", n=300)               # more than the 35 invented real rows: they are drawn with replacement
    descs = [" | ".join(e["tx"]["remittance_information"]) for e in fx["transactions"]]
    assert any(re.match(r"^CARTE \d{2}/\d{2} \S+ \S+$", d) for d in descs)
    assert any(re.search(r"\d+,\d{2} USD COURS \d,\d{2}$", d) for d in descs)           # the foreign-currency line keeps its form
    assert any(d.startswith("RET DAB ") for d in descs) and any(d.startswith("ANN CARTE ") for d in descs)
    for e in fx["transactions"]:
        ref = e["tx"]["entry_reference"]
        assert is_position_ref(ref) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00-\d+", ref)     # Fortuneo's positional references keep their position
    lengths = Counter(len(w) for d in descs for w in d.split() if w.isalpha() and w.isupper())
    assert lengths                                                   # words keep their length: truncation artefacts stay realistic


def test_card_dates_keep_their_relation_to_the_booking_date(cfg, real):
    from coach.classify.parsers.common import ddmm_date
    fx, _ = synth(real, cfg, "fortuneo", n=60)
    for e in fx["transactions"]:
        tx = e["tx"]
        m = re.match(r"^CARTE (\d{2}/\d{2}) ", tx["remittance_information"][0])
        if m and e["expect"]["tx_type"] == "card" and tx["status"] == "BOOK":
            op = ddmm_date(m.group(1), tx["booking_date"])
            assert op is not None and 0 <= (__import__("datetime").date.fromisoformat(tx["booking_date"])
                                           - __import__("datetime").date.fromisoformat(op)).days <= 31


def test_amounts_are_redrawn_but_keep_their_digits_and_roundness(cfg, real):
    fx, _ = synth(real, cfg, "fortuneo", n=60)
    amounts = [e["tx"]["transaction_amount"]["amount"] for e in fx["transactions"]]
    assert all(re.fullmatch(r"\d+\.\d{2}", a) for a in amounts)
    assert "2500.00" not in amounts and "87.10" not in amounts and "60.00" not in amounts          # the real amounts are gone
    assert any(a.endswith(".00") for a in amounts)                    # the round ones (a transfer, a cash withdrawal) stay round


def test_the_same_real_merchant_is_the_same_invented_merchant(cfg, real):
    fx, _ = synth(real, cfg, "fortuneo", n=40)
    shops = Counter(" ".join(e["tx"]["remittance_information"][0].split()[2:4]) for e in fx["transactions"]
                    if e["expect"]["tx_type"] == "card" and e["tx"]["remittance_information"][0].startswith("CARTE"))
    assert len(shops) <= 4 and max(shops.values()) >= 5                # two real merchants repeated many times stay two invented merchants


def test_the_output_is_deterministic_for_a_seed(cfg, real):
    a, _ = synth(real, cfg, "caisse_epargne", seed=5)
    b, _ = synth(real, cfg, "caisse_epargne", seed=5)
    c, _ = synth(real, cfg, "caisse_epargne", seed=6)
    assert json.dumps(a) == json.dumps(b) and json.dumps(a) != json.dumps(c)


def test_every_invented_descriptor_still_parses_to_the_type_of_its_real_shape(cfg, real):
    for bank in ("fortuneo", "caisse_epargne", "revolut"):
        fx, rep = synth(real, cfg, bank, n=50)
        house = Household(holders=[set(h) for h in fx["household"]["holders"]], family=set(fx["household"]["family"]),
                          first_names=set(fx["household"]["first_names"]), own_banks=fx["household"]["own_banks"])
        for e in fx["transactions"]:
            tx = e["tx"]
            if tx["status"] == "PDNG":
                continue
            amount = float(tx["transaction_amount"]["amount"]) * (-1 if tx["credit_debit_indicator"] == "DBIT" else 1)
            party = (tx["creditor"] if amount < 0 else tx["debtor"]) or {}
            p = parse_tx(RawTx(" | ".join(tx["remittance_information"]), amount, tx["booking_date"], "EUR", party.get("name") or "",
                               (tx.get("bank_transaction_code") or {}).get("code") or "", e["account_type"], fx["bank"], tx), house, bank=fx["bank"])
            assert p["tx_type"] == e["expect"]["tx_type"] != "other", (bank, e["expect"], tx["remittance_information"])


def test_a_leak_stops_everything_before_a_file_is_written(cfg, real, tmp_path, monkeypatch):
    monkeypatch.setattr(F.Mutator, "word", lambda self, tok: "QUORBEX")           # a bug that lets a real word through
    with pytest.raises(F.SynthLeak):
        synth(real, cfg, "fortuneo")
    real.close()
    out = tmp_path / "leak.json"
    with pytest.raises(SystemExit) as e:
        main(["--insecure", "--config", str(cfg.config_path), "eval", "fixtures", "synth", "--bank", "fortuneo", "--out", str(out)])
    assert "nothing written" in str(e.value) and not out.exists()


def test_a_household_term_is_caught_even_when_it_is_a_format_word(cfg, real):
    terms, _ = F.collect_terms(real, cfg)
    assert "SELVARIN" in terms.forbidden and "SELVARIN" in terms.household_terms
    assert "QUORBEX" in terms.forbidden and "CARTE" not in terms.forbidden and "CARTE" in terms.structural
    fx = {"account": {"name": "M OU MME SELVARIN"}, "household": {}, "transactions": []}
    assert F.check_output(fx, terms) == [("household_term", "")]
    fx = {"account": {}, "household": {}, "transactions": [{"tx": {"remittance_information": ["CARTE 01/02 QUORBEX"], "creditor": {}, "debtor": {}}}]}
    assert ("real_word", "") in F.check_output(fx, terms)
    fx = {"account": {}, "household": {}, "transactions": [{"tx": {"remittance_information": ["REF 123456789012"], "creditor": {}, "debtor": {}}}]}
    assert ("real_digits", "") in F.check_output(fx, terms)
    fx = {"account": {}, "household": {}, "transactions": [{"tx": {"remittance_information": ["FR7630006000011234567890189"], "creditor": {}, "debtor": {}}}]}
    assert ("valid_iban", "") in F.check_output(fx, terms)


def test_the_format_words_are_not_household_terms_so_a_given_name_is_never_kept(cfg, real):
    terms, house = F.collect_terms(real, cfg)
    assert not terms.structural & terms.household_terms
    real.execute("UPDATE accounts SET name='M OU MME SALAIRE LOYER' WHERE uid='fo'")      # a household whose "names" are format words
    real.commit()
    terms2, _ = F.collect_terms(real, cfg)
    assert "SALAIRE" not in terms2.structural and "LOYER" not in terms2.structural


def test_the_command_writes_the_fixture_and_reports_the_check(cfg, real, tmp_path, capsys):
    real.close()
    out = tmp_path / "out" / "fx.json"
    main(["--insecure", "--config", str(cfg.config_path), "eval", "fixtures", "synth", "--bank", "revolut", "--n", "30", "--out", str(out)])
    said = capsys.readouterr().out
    assert "real-token check PASSED" in said and "0 finding(s)" in said
    d = json.loads(out.read_text())
    assert d["synthetic"] is True and d["parser"] == "revolut" and len(d["transactions"]) >= 2


def test_the_command_refuses_to_write_inside_the_data_folders(cfg, real, capsys):
    real.close()
    for bad in (cfg.data_dir / "fx.json", cfg.memory_dir / "fx.json"):
        with pytest.raises(SystemExit) as e:
            main(["--insecure", "--config", str(cfg.config_path), "eval", "fixtures", "synth", "--bank", "cic", "--out", str(bad)])
        assert "refusing to write" in str(e.value)


def test_an_unknown_bank_and_an_empty_database_are_clear_errors(cfg, real):
    with pytest.raises(F.SynthError):
        F.synthesize(real, cfg, "cic", 10)                          # the invented database has no CIC account
    with pytest.raises(F.SynthError):
        F.synthesize(real, cfg, "nonesuch", 10)


# ---------------------------------------------------------------- the committed fixtures

def load(bank):
    return json.loads((FIXTURES / f"{bank}.json").read_text())


def household_of(fx):
    return Household(holders=[set(h) for h in fx["household"]["holders"]], family=set(fx["household"]["family"]),
                     first_names=set(fx["household"]["first_names"]), own_banks=fx["household"]["own_banks"])


def pages(entries, size):
    chunks = [entries[i:i + size] for i in range(0, len(entries), size)] or [[]]
    return [{"transactions": c, **({"continuation_key": f"K{i + 1}"} if i < len(chunks) - 1 else {})} for i, c in enumerate(chunks)]


def write_household(cfg, fx):
    """The household of the fixture as memory/household.yaml (members written 'given family'), so that the database-level parse sees the same
    holders as the fixture's own Household."""
    firsts = set(fx["household"]["first_names"])
    members = []
    for i, h in enumerate(fx["household"]["holders"]):
        given = [t for t in sorted(h) if t in firsts]
        rest = [t for t in sorted(h) if t not in given]
        members.append(f"  - {{id: m{i}, name: {' '.join(given + rest)}, role: adult}}")
    cfg.memory_dir.mkdir(parents=True, exist_ok=True)
    (cfg.memory_dir / "household.yaml").write_text("members:\n" + "\n".join(members) + "\n")


def build_db(cfg, fx, page_size=25, pending_from_booked=0):
    """The fixture's bank in a database, synced through the mocked API. Returns (con, client, entries by account uid)."""
    con = connect(cfg, insecure=True, create=True)
    acc = fx["account"]
    add_bank(con, "s1", fx["bank"], acc["country"], [("a1", acc["iban"], acc["name"])])
    for i, other in enumerate(sorted(set(fx["household"]["own_banks"].values()) - {fx["bank"]})):        # the household's accounts at the OTHER banks
        add_bank(con, f"sx{i}", other, "FR", [(f"x{i}", f"FR76000000000000000000{i:04d}", "CPT EXTRA")])
    by_acct = {"a1": []}
    if any(e["account_type"] == "SVGS" for e in fx["transactions"]):
        add_bank(con, "s2", fx["bank"], acc["country"], [("a2", acc["iban"][:-1] + "9", "Savings pocket")])
        con.execute("UPDATE accounts SET cash_account_type='SVGS' WHERE uid='a2'")
        con.commit()
        by_acct["a2"] = []
    for e in fx["transactions"]:
        by_acct["a2" if e["account_type"] == "SVGS" and "a2" in by_acct else "a1"].append(e)
    client = FakeClient({u: pages([e["tx"] for e in es], page_size) * 2 for u, es in by_acct.items()})
    return con, client, by_acct


@pytest.mark.parametrize("bank", list(BANK_FILES))
def test_committed_fixture_is_invented_and_well_formed(bank):
    fx = load(bank)
    assert fx["synthetic"] is True and fx["parser"] == bank and fx["bank"] == BANK_FILES[bank] and len(fx["transactions"]) >= 150
    assert not F.iban_valid(fx["account"]["iban"])
    text = (FIXTURES / f"{bank}.json").read_text()
    assert all(not F.iban_valid(x) for x in re.findall(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b", text))
    assert text.isascii()
    refs = [e["tx"]["entry_reference"] for e in fx["transactions"]]
    assert len(set(refs)) == len(refs)


@pytest.mark.parametrize("bank", list(BANK_FILES))
def test_parser_coverage_every_line_is_recognised_and_gets_its_type(bank):
    fx = load(bank)
    house = household_of(fx)
    types = Counter()
    for e in fx["transactions"]:
        tx = e["tx"]
        if tx["status"] == "PDNG":
            continue
        amount = float(tx["transaction_amount"]["amount"]) * (-1 if tx["credit_debit_indicator"] == "DBIT" else 1)
        party = (tx["creditor"] if amount < 0 else tx["debtor"]) or {}
        p = parse_tx(RawTx(" | ".join(tx["remittance_information"]), amount, tx["booking_date"], tx["transaction_amount"]["currency"],
                           party.get("name") or "", (tx.get("bank_transaction_code") or {}).get("code") or "", e["account_type"], fx["bank"], tx),
                     house, bank=fx["bank"])
        types[p["tx_type"]] += 1
        assert p["tx_type"] != "other", tx["remittance_information"]
        assert p["tx_type"] == e["expect"]["tx_type"], (tx["remittance_information"], p["tx_type"], e["expect"])
        assert p["merchant_key"] == p["merchant_key"].upper() and p["parser"] == bank
    assert sum(types.values()) >= 140 and len(types) >= 3          # a real mix of shapes, not one line repeated


@pytest.mark.parametrize("bank", list(BANK_FILES))
def test_through_the_ingest_and_normalize_nothing_is_other_and_the_types_match(cfg, bank):
    fx = load(bank)
    write_household(cfg, fx)
    con, client, by_acct = build_db(cfg, fx)
    booked = [e for e in fx["transactions"] if e["tx"]["status"] == "BOOK"]
    for uid in by_acct:
        r = sync_account(con, client, uid, False, True, out=lambda *_: None)
        assert r["status"] == "ok"
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == len(booked)
    normalize_all(con, cfg.memory_dir)
    got = dict(con.execute("SELECT t.entry_reference, e.tx_type FROM transactions t JOIN tx_enriched e USING(tx_key)").fetchall())
    assert "other" not in got.values()
    same = sum(1 for e in booked if got[e["tx"]["entry_reference"]] == e["expect"]["tx_type"])
    assert same / len(booked) >= 0.97, (bank, same, len(booked))    # the database rebuilds the household from household.yaml: at most a stray transfer differs


@pytest.mark.parametrize("bank", list(BANK_FILES))
def test_keys_are_stable_across_page_sizes_and_unique(tmp_path, bank):
    fx = load(bank)
    sets = []
    for size, name in ((10, "a"), (37, "b")):
        sub = tmp_path / name
        (sub / "memory").mkdir(parents=True)
        (sub / "config.toml").write_text('data_dir = "data"\nmemory_dir = "memory"\n')
        cfg2 = load_config(sub / "config.toml", env={})
        con, client, by_acct = build_db(cfg2, fx, page_size=size)
        for uid in by_acct:
            sync_account(con, client, uid, False, True, out=lambda *_: None)
        keys = [r[0] for r in con.execute("SELECT tx_key FROM transactions ORDER BY tx_key")]
        assert len(keys) == len(set(keys))
        sets.append(keys)
        con.close()
    assert sets[0] == sets[1] and len(sets[0]) == len([e for e in fx["transactions"] if e["tx"]["status"] == "BOOK"])


@pytest.mark.parametrize("bank", list(BANK_FILES))
def test_pagination_dedup_and_pending_with_the_mocked_api(cfg, bank):
    fx = load(bank)
    con, client, by_acct = build_db(cfg, fx, page_size=25)
    uid = "a1"
    entries = by_acct[uid]
    booked = [e for e in entries if e["tx"]["status"] == "BOOK"]
    pending = [e for e in entries if e["tx"]["status"] == "PDNG"]
    first = sync_account(con, client, uid, False, True, out=lambda *_: None)
    n_pages = math.ceil(len(entries) / 25)
    assert first["pages"] == n_pages and first["new"] == len(booked) and first["pending"] == len(pending)
    tx_calls = [c for c in client.calls if c[1].endswith("/transactions")]
    assert tx_calls[0][2] == {"strategy": "longest"}
    assert tx_calls[1][2] == {"strategy": "longest", "continuation_key": "K1"}          # the original query is kept on every page
    assert tx_calls[n_pages - 1][2]["continuation_key"] == f"K{n_pages - 1}"
    assert con.execute("SELECT COUNT(*) FROM pending_transactions").fetchone()[0] == len(pending)   # pending is never in the history
    # the same pages again: nothing is added twice
    second = sync_account(con, client, uid, False, True, out=lambda *_: None)
    assert second["new"] == 0 and con.execute("SELECT COUNT(*) FROM transactions WHERE account_uid='a1'").fetchone()[0] == len(booked)


@pytest.mark.parametrize("bank", list(BANK_FILES))
def test_a_pending_payment_is_replaced_by_its_booked_version(cfg, bank):
    fx = load(bank)
    con = connect(cfg, insecure=True, create=True)
    acc = fx["account"]
    add_bank(con, "s1", fx["bank"], acc["country"], [("a1", acc["iban"], acc["name"])])
    items = [e["tx"] for e in fx["transactions"] if e["tx"]["status"] == "BOOK" and e["account_type"] != "SVGS"][:12]
    as_pending = [{**t, "status": "PDNG"} for t in items[:3]]
    sync1 = [{"transactions": items[3:] + as_pending}]
    sync2 = [{"transactions": items}]                              # the three are now booked
    client = FakeClient({"a1": sync1 + sync2})
    sync_account(con, client, "a1", False, True, out=lambda *_: None)
    assert con.execute("SELECT COUNT(*) FROM pending_transactions").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == len(items) - 3
    sync_account(con, client, "a1", False, True, out=lambda *_: None)
    assert con.execute("SELECT COUNT(*) FROM pending_transactions").fetchone()[0] == 0            # replaced, not appended
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == len(items)


@pytest.mark.parametrize("bank", list(BANK_FILES))
def test_a_rate_limit_in_the_middle_of_the_pages_rolls_everything_back(cfg, bank):
    from coach.ingest.client import ApiError
    fx = load(bank)
    con = connect(cfg, insecure=True, create=True)
    acc = fx["account"]
    add_bank(con, "s1", fx["bank"], acc["country"], [("a1", acc["iban"], acc["name"])])
    items = [e["tx"] for e in fx["transactions"] if e["account_type"] != "SVGS"]
    client = FakeClient({"a1": [pages(items, 20)[0], ApiError(429, "ASPSP_RATE_LIMIT_EXCEEDED")]})
    r = sync_account(con, client, "a1", False, True, out=lambda *_: None)
    assert r["status"] == "failed" and r["http_status"] == 429
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_the_committed_fixtures_cover_the_shapes_the_parsers_are_written_for():
    seen = {b: Counter(e["expect"]["tx_type"] for e in load(b)["transactions"]) for b in BANK_FILES}
    assert {"card", "atm", "wero_out"} <= set(seen["fortuneo"])
    assert {"direct_debit", "loan_payment", "bank_fee"} <= set(seen["caisse_epargne"])
    assert {"loan_payment", "bank_fee", "transfer_in"} <= set(seen["cic"])
    assert {"card", "savings_internal", "topup", "bank_fee"} <= set(seen["revolut"])
