import dataclasses
import json
import socket
from argparse import Namespace
from datetime import datetime, timedelta, timezone

import pytest

from coach import db as dbm, transfers as tm
from coach.classify import commands as cc
from coach.classify.parser import holder_sets, owner_tokens, parse
from coach.classify.rules import categorised
from coach.cli import main
from coach.db import connect
from coach.ingest import accounts as acc, auth, callback as cb, commands as ic
from coach.ingest.imports import core
from coach.ingest.imports.profiles import Profile
from helpers import FakeClient, add_bank, add_tx, session_payload

IBAN_CE = "FR7600000000000000000000010"
IBAN_FO = "FR7600000000000000000000020"


@pytest.fixture
def con(cfg):
    return connect(cfg, insecure=True, create=True)


def two_banks(con, card_name="Visa Premier"):
    add_bank(con, "s-ce", "Caisse", "FR", [("ce", IBAN_CE, "M OU MME DURAND PAUL")])
    add_bank(con, "s-fo", "Fortuneo", "FR", [("fo", IBAN_FO, "M OU MME DURAND PAUL")])
    con.execute("INSERT INTO accounts(uid, session_id, name, currency, cash_account_type, raw, bank) "
                "VALUES ('ce-card','s-ce',?, 'EUR','CARD','{}','Caisse')", (card_name,))
    con.commit()


# ---------------------------------------------------------------- M1

def test_m1_product_named_account_does_not_pollute_holder_tokens(con):
    two_banks(con)
    assert holder_sets(con) == [{"DURAND", "PAUL"}]
    assert owner_tokens(con) == {"DURAND", "PAUL"}
    owner, holders = owner_tokens(con), holder_sets(con)
    for desc in ("VIR M DURAND PAUL", "VIR M OU MME DURAND PAUL", "VIR PAUL DURAND"):
        assert parse(desc, -10, "2026-10-01", owner, holders)["tx_type"] == "internal_transfer"
    # tokens of ONE holder are required, not any mix of tokens present in the account table
    assert parse("VIR PREMIER PAUL", -10, "2026-10-01", owner, holders)["tx_type"] != "internal_transfer"


def test_m1_normalize_keeps_own_transfers_internal_with_a_product_named_card(cfg, con):
    two_banks(con)
    for i in range(3):
        add_tx(con, "fo", f"fo:{i}", f"2026-09-0{i + 1}", 100.0, "VIR M OU MME DURAND PAUL")
    cc.cmd_normalize(Namespace(insecure=True), cfg)
    assert {r[0] for r in con.execute("SELECT tx_type FROM tx_enriched")} == {"internal_transfer"}


def test_m1_visa_premier_style_names_and_card_types_are_skipped_but_real_holders_and_owner_count(con):
    add_bank(con, "s", "B", "FR", [("a", "FR1", "MME LUCIE DURAND")])
    for n, t in (("Carte Visa", None), ("LIVRET A", None), ("Joint Account", None), ("Anything Odd", "CARD")):
        con.execute("INSERT INTO accounts(uid, session_id, name, cash_account_type, raw) VALUES (?,?,?,?,?)",
                    ("x-" + n, "s", n, t, "{}"))
    con.execute("INSERT INTO accounts(uid, session_id, name, owner, raw) VALUES ('kid','s','Kids','Lea','{}')")
    con.commit()
    # H2: a one-token holder ('Lea') is never an own-account holder, it only counts for family detection
    assert sorted(map(sorted, holder_sets(con))) == [["DURAND", "LUCIE"]]
    assert "LEA" in owner_tokens(con)


# ---------------------------------------------------------------- M2

def test_m2_salary_line_ending_with_holder_name_is_not_an_own_transfer():
    owner, holders = {"DURAND", "PAUL"}, [{"DURAND", "PAUL"}]
    r = parse("VIR SEPA ACME SAS SALAIRE OCT M PAUL DURAND", 2500, "2026-10-01", owner, holders)
    assert r["tx_type"] == "transfer_in"                        # neither internal_transfer nor a family transfer
    r = parse("VIR SEPA ACME SAS SALAIRE OCT M PAUL DURAND", 2500, "2026-10-01", owner, None)
    assert r["tx_type"] == "transfer_in"                        # also with the legacy single-set call
    assert parse("VIR SEPA M PAUL DURAND", -5, "2026-10-01", owner, holders)["tx_type"] == "internal_transfer"
    assert parse("VIR INST M     PAUL DURAND", -5, "2026-10-01", owner, holders)["tx_type"] == "internal_transfer"
    assert parse("VIR Virement avec Livret A", -5, "2026-10-01", owner, holders)["tx_type"] == "internal_transfer"


def test_m2_salary_is_never_auto_linked_to_a_same_amount_rent_payment(con):
    two_banks(con)
    add_tx(con, "ce", "sal", "2026-10-01", 2500.0, "VIR SEPA ACME SAS SALAIRE OCT M PAUL DURAND", "transfer_in")
    add_tx(con, "fo", "rent", "2026-10-02", -2500.0, "VIR SEPA LOYER OCTOBRE AGENCE X", "transfer_out")
    res = tm.match_transfers(con)
    assert res.linked == [] and tm.find_pairs(con).linked == []
    assert con.execute("SELECT COUNT(*) FROM transfer_links").fetchone()[0] == 0


def test_m2_single_internal_transfer_leg_is_not_strong(con):
    two_banks(con)
    add_tx(con, "ce", "o", "2026-10-01", -400.0, "VIR M DURAND PAUL", "internal_transfer")
    add_tx(con, "fo", "i", "2026-10-01", 400.0, "VIR DE QUELQU UN", "transfer_in")
    res = tm.find_pairs(con)
    assert res.linked == [] and len(res.proposals) == 1 and res.proposals[0]["strong"] is False


def test_m2_own_transfer_ce_to_fortuneo_is_still_auto_linked(con):
    two_banks(con)
    add_tx(con, "ce", "o", "2026-10-01", -400.0, "VIR M DURAND PAUL", "internal_transfer")
    add_tx(con, "fo", "i", "2026-10-02", 400.0, "VIR M OU MME DURAND PAUL", "internal_transfer")
    assert len(tm.match_transfers(con).linked) == 1
    add_tx(con, "ce", "o2", "2026-10-05", -50.0, "VIR VERS COMPTE", "transfer_out")        # IBAN route
    add_tx(con, "fo", "i2", "2026-10-05", 50.0, "VIR DE CE", "transfer_in")
    con.execute("UPDATE transactions SET description='VIR VERS FR7600000000000000000000020' WHERE tx_key='o2'")
    con.commit()
    assert len(tm.match_transfers(con).linked) == 1


def test_m2_income_label_or_annotation_excludes_a_leg_even_under_a_type_rule(con):
    two_banks(con)
    add_tx(con, "ce", "o", "2026-10-01", -400.0, "VIR M DURAND PAUL", "internal_transfer")
    add_tx(con, "fo", "i", "2026-10-01", 400.0, "VIR M DURAND PAUL", "internal_transfer")
    assert len(tm.find_pairs(con).linked) == 1
    con.execute("INSERT INTO merchants VALUES ('VIR M DURAND PAUL','Employer','income.salary',0.9,0,'llm',NULL,'t')")   # merchant label
    con.commit()
    assert tm.find_pairs(con).linked == [] and tm.find_pairs(con).proposals == []
    con.execute("DELETE FROM merchants")
    con.commit()
    ann = [{"id": "a1", "match": {"tx_keys": ["o"]}, "category": "income.other"}]                       # memory annotation
    assert tm.find_pairs(con, annotations=ann).linked == []
    assert len(tm.find_pairs(con, annotations=[]).linked) == 1


# ---------------------------------------------------------------- M3

def pockets(sid, pairs, typ=None):
    return {"session_id": sid, "aspsp": {"name": "Revolut", "country": "LT"},
            "access": {"valid_until": "2099-01-01T00:00:00Z"},
            "accounts": [{"uid": u, "name": "Main", "currency": c, "account_id": {"iban": "LT0000000000000001"},
                          **({"cash_account_type": typ} if typ else {})} for u, c in pairs]}


def test_m3_missing_type_does_not_make_same_currency_pockets_ambiguous(con):
    auth.complete_auth(con, FakeClient(codes={"C": pockets("s-old", [("e1", "EUR"), ("e2", "EUR"), ("u1", "USD")])}), "C")
    # (two EUR pockets on one IBAN is itself ambiguous on reconnect...)
    con.execute("DELETE FROM accounts WHERE uid='e2'")
    con.commit()
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id) "
                "VALUES ('st','Revolut','LT',?,'s-old')", (datetime.now(timezone.utc).isoformat(),))
    con.commit()
    info = auth.complete_auth(con, FakeClient(codes={"C2": pockets("s-new", [("n-eur", "EUR"), ("n-usd", "USD")])}), "C2", "st")
    assert [(a["uid"], a["needs_review"]) for a in info["accounts"]] == [("e1", False), ("u1", False)]


def test_m3_exact_type_match_beats_wildcard(con):
    add_bank(con, "s-old", "Revolut", "LT", [("e-typed", "LT0000000000000001", "Main"), ("e-untyped", "LT0000000000000001", "Main")],
             "2020-01-01T00:00:00+00:00")
    con.execute("UPDATE accounts SET cash_account_type='CACC' WHERE uid='e-typed'")
    con.execute("UPDATE accounts SET cash_account_type=NULL, currency=NULL WHERE uid='e-untyped'")
    con.commit()
    info = auth.complete_auth(con, FakeClient(codes={"C": pockets("s-new", [("n1", "EUR")], typ="CACC")}), "C")
    assert info["accounts"][0]["uid"] == "e-typed" and not info["accounts"][0]["needs_review"]


def test_m3_finish_without_replace_retires_dead_session_and_reports_orphans(con):
    add_bank(con, "s-dead", "Bank", "FR", [("a1", IBAN_CE, "A"), ("a2", IBAN_FO, "B")], "2020-01-01T00:00:00+00:00")
    info = auth.complete_auth(con, FakeClient(codes={"C": session_payload("s-new", "Bank", "FR", [("n1", IBAN_CE, "A")])}), "C")
    assert info["accounts"][0]["uid"] == "a1" and info["orphans"] == ["a2"]
    assert info["retired"] == []                                    # a2 still hangs on it: reported, not hidden
    out = []
    auth.print_completion(info, out.append)
    assert any("a2" in l and "not returned" in l for l in out)
    add_bank(con, "s-dead2", "Bank2", "FR", [("b1", "FR7600000000000000000000099", "A")], "2020-01-01T00:00:00+00:00")
    info = auth.complete_auth(con, FakeClient(codes={"C2": session_payload("s-n2", "Bank2", "FR", [("m1", "FR7600000000000000000000099", "A")])}), "C2")
    assert info["retired"] == ["s-dead2"] and info["orphans"] == []


# ---------------------------------------------------------------- M4

def card_topup(con):
    two_banks(con)
    add_tx(con, "fo", "card", "2026-10-01", -100.0, "CARTE 30/09 REVOLUT**1234", "card")
    con.execute("UPDATE tx_enriched SET merchant_key='REVOLUT' WHERE tx_key='card'")
    add_tx(con, "ce", "topup", "2026-10-01", 100.0, "Top-up by *1234", "transfer_in")
    con.commit()


def test_m4_card_top_up_is_proposed_never_auto_linked(con):
    card_topup(con)
    res = tm.match_transfers(con)
    assert res.linked == [] and len(res.proposals) == 1 and res.proposals[0]["topup"]
    assert con.execute("SELECT COUNT(*) FROM transfer_links").fetchone()[0] == 0
    assert "card top-up" in tm.format_proposals(res)
    tm.link_transfer(con, "card", "topup")                                  # the user can confirm it
    assert categorised(con, rules={"type_rules": {"card": "shopping.other"}, "merchant_rules": []}, annotations=[]).__next__()


def test_m4_topup_list_is_configurable_and_plain_card_payments_stay_excluded(con):
    card_topup(con)
    assert tm.find_pairs(con, topups=("LYDIA",)).proposals == []            # REVOLUT not in the list
    add_tx(con, "fo", "shop", "2026-10-03", -20.0, "CARTE 02/10 BOULANGERIE", "card")
    add_tx(con, "ce", "in20", "2026-10-03", 20.0, "VIR REMBOURSEMENT", "transfer_in")
    assert [p["debit"].tx_key for p in tm.find_pairs(con).proposals] == ["card"]


def test_m4_config_validation(tmp_path):
    from coach.config import ConfigError, load_config
    p = tmp_path / "config.toml"
    p.write_text('[transfers]\ntopup_merchants = ["revolut", "wise"]\n')
    assert load_config(p, env={}).transfer_topup_merchants == ("REVOLUT", "WISE")
    p.write_text('[transfers]\ntopup_merchants = "revolut"\n')
    with pytest.raises(ConfigError):
        load_config(p, env={})


# ---------------------------------------------------------------- M5

ODD_PAYLOADS = [
    {"session_id": "o1", "accounts": [{"uid": "u1"}]},                                            # no aspsp / access / details
    {"session_id": "o2", "aspsp": {"name": "Bank"}, "access": {}, "accounts": [
        {"uid": "u2", "account_id": None, "name": None, "currency": None}]},
    {"session_id": "o3", "aspsp": {"name": "Bank", "country": "FR"}, "access": None, "accounts": None},
    {"session_id": "o4", "aspsp": None, "accounts": [{"uid": "u4", "account_id": {"other": {"identification": "X1"}}},
                                                      {"name": "no uid"}, "garbage", {"uid": "u5", "product": "Card"}]},
]


def pending(con, state="st", replaces=None):
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id, "
                "valid_until_requested) VALUES (?, 'Bank','FR',?,?, '2027-02-02T00:00:00+00:00')",
                (state, datetime.now(timezone.utc).isoformat(), replaces))
    con.commit()


@pytest.mark.parametrize("payload", ODD_PAYLOADS)
def test_m5_odd_payloads_are_stored_with_fallbacks(con, payload):
    pending(con)
    info = auth.complete_auth(con, FakeClient(codes={"C": payload}), "C", "st")
    row = con.execute("SELECT aspsp_name, aspsp_country, valid_until FROM sessions").fetchone()
    assert row == ("Bank", "FR", "2027-02-02T00:00:00+00:00")                  # from the pending row / request
    assert con.execute("SELECT session_response FROM pending_auth").fetchone()[0] is None   # cleaned after success
    assert con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == len([a for a in (payload.get("accounts") or [])
                                                                              if isinstance(a, dict) and a.get("uid")])
    out = []
    auth.print_completion(info, out.append)                                    # prints without raising


def test_m5_valid_until_from_response_wins_when_present(con):
    pending(con)
    auth.complete_auth(con, FakeClient(codes={"C": session_payload("s1", "Bank", "FR", [("u", None, None)], "2099-05-05T00:00:00Z")}), "C", "st")
    assert con.execute("SELECT valid_until FROM sessions").fetchone()[0] == "2099-05-05T00:00:00Z"


def test_m5_storage_failure_keeps_the_response_and_replay_stores_it(con, monkeypatch):
    pending(con, replaces=None)
    real = acc.store_session
    monkeypatch.setattr(auth, "store_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    with pytest.raises(auth.ConnectError) as e:
        auth.complete_auth(con, FakeClient(codes={"C": session_payload("s-lost", "Bank", "FR", [("u1", IBAN_CE, "A")])}), "C", "st")
    msg = str(e.value)
    assert "s-lost" in msg and "coach finish --replay st" in msg and "disk full" in msg
    assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    saved = con.execute("SELECT session_response, completed_at IS NOT NULL FROM pending_auth").fetchone()
    assert json.loads(saved[0])["session_id"] == "s-lost" and saved[1]            # code consumed, response kept
    with pytest.raises(auth.ConnectError, match="already completed"):
        auth.complete_auth(con, FakeClient(codes={"C": {}}), "C", "st")
    monkeypatch.setattr(auth, "store_session", real)
    info = auth.replay_auth(con, "st")
    assert info["session_id"] == "s-lost" and con.execute("SELECT uid FROM accounts").fetchone()[0] == "u1"
    with pytest.raises(auth.ConnectError, match="Nothing to replay"):
        auth.replay_auth(con, "st")
    with pytest.raises(auth.ConnectError, match="Unknown state"):
        auth.replay_auth(con, "nope")


def test_m5_bare_code_failure_is_replayable_through_an_adhoc_state(con, monkeypatch):
    monkeypatch.setattr(auth, "store_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(auth.ConnectError) as e:
        auth.complete_auth(con, FakeClient(codes={"C": session_payload("s-b", "Bank", "FR", [("u1", IBAN_CE, "A")])}), "C")
    state = str(e.value).split("--replay ")[1].split("`")[0]
    assert state.startswith("adhoc-")
    monkeypatch.undo()
    assert auth.replay_auth(con, state)["session_id"] == "s-b"


def test_m5_bank_call_failure_releases_the_claim(con):
    pending(con)
    with pytest.raises(KeyError):
        auth.complete_auth(con, FakeClient(codes={}), "X", "st")
    assert con.execute("SELECT completed_at FROM pending_auth").fetchone()[0] is None
    auth.complete_auth(con, FakeClient(codes={"C": ODD_PAYLOADS[0]}), "C", "st")
    # bare-code bank failure leaves no ad-hoc row behind
    with pytest.raises(KeyError):
        auth.complete_auth(con, FakeClient(codes={}), "X")
    assert con.execute("SELECT COUNT(*) FROM pending_auth WHERE state LIKE 'adhoc-%'").fetchone()[0] == 0


def test_m5_cli_finish_replay(cfg, con, monkeypatch, capsys):
    pending(con)
    monkeypatch.setattr(auth, "store_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(auth.ConnectError):
        auth.complete_auth(con, FakeClient(codes={"C": session_payload("s-c", "Bank", "FR", [("u1", IBAN_CE, "A")])}), "C", "st")
    monkeypatch.undo()
    con.close()
    main(["--config", str(cfg.config_path), "--insecure", "finish", "--replay", "st"])
    assert "s-c" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="redirect URL"):
        main(["--config", str(cfg.config_path), "--insecure", "finish"])


# ---------------------------------------------------------------- M6

def test_m6_import_detects_existing_api_account_with_null_hash(con, tmp_path):
    add_bank(con, "s1", "Bank", "FR", [("api1", "FR76 0000 0000 0000 0000 0000 010", "CHK")])
    assert con.execute("SELECT identification_hash FROM accounts").fetchone()[0] is None      # as stored by old code
    f = tmp_path / "a.ofx"
    f.write_text("<OFX><CURDEF>EUR<ACCTID>FR7600000000000000000000010<STMTTRN><DTPOSTED>20260901<TRNAMT>-1.00"
                 "<FITID>1<NAME>X</STMTTRN></OFX>")
    with pytest.raises(core.ImportFailed, match="already belongs to account api1"):
        core.import_file(con, f, "new:Dup")


# ---------------------------------------------------------------- N1

def test_n1_old_pending_auth_is_not_accepted(con):
    pending(con, "fresh")
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at) VALUES ('old','Bank','FR',?)",
                ((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds"),))
    con.commit()
    with pytest.raises(auth.ConnectError, match="expired"):
        auth.complete_auth(con, FakeClient(codes={"C": ODD_PAYLOADS[0]}), "C", "old")
    auth.complete_auth(con, FakeClient(codes={"C": ODD_PAYLOADS[0]}), "C", "fresh")
    assert auth.is_expired("2026-01-01T00:00:00+00:00") and not auth.is_expired("t") and not auth.is_expired(None)


def test_n1_callback_refuses_expired_state_and_keeps_waiting(cfg, con, tmp_path):
    cfg2 = dataclasses.replace(cfg, eb_app_id="a", eb_private_key_path="k",
                               eb_redirect_url="https://127.0.0.1:0/callback")
    box = {}

    class C(FakeClient):
        pass
    # drive the handler through connect_flow's server without a socket: age the row, then call the closure
    import threading, requests
    ready = threading.Event()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    cfg2 = dataclasses.replace(cfg2, eb_redirect_url=f"https://127.0.0.1:{port}/callback")

    def work():
        c = connect(cfg2, insecure=True)
        box["code"] = auth.connect_flow(c, C(codes={"X": ODD_PAYLOADS[0]}), cfg2, "Bank", "FR", 30, no_browser=True,
                                        timeout=3, out=lambda *_: None,
                                        on_listening=lambda p, st: (box.update(port=p, state=st), ready.set()))
    t = threading.Thread(target=work, daemon=True)
    t.start()
    assert ready.wait(10)
    c2 = connect(cfg2, insecure=True)
    c2.execute("UPDATE pending_auth SET created_at=?", ((datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(),))
    c2.commit()
    r = requests.get(f"https://127.0.0.1:{box['port']}/callback", params={"state": box["state"], "code": "X"},
                     verify=str(cfg2.tls_dir / cb.CERT_NAME), timeout=5)
    assert r.status_code == 400
    t.join(10)
    assert box["code"] == 1 and c2.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_n1_migration_marks_legacy_state_completed_only_when_its_session_exists(cfg, tmp_path):
    cfg.db_path.parent.mkdir(parents=True)
    c = connect(cfg, insecure=True, create=True)
    mig = [m for m in dbm.available_migrations() if m.version == 5][0]
    stmts = dbm.split_sql(mig.sql)
    c.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw) "
              "VALUES ('s1','Fortuneo','FR','2099','2026-10-04T09:22:11+00:00','{}')")
    for st, bank, at in (("fo-state", "Fortuneo", "2026-10-04T09:22:10+00:00"), ("ce-state", "Caisse", "2026-10-04T11:43:31+00:00"),
                         ("fo-later", "Fortuneo", "2026-10-05T09:00:00+00:00")):
        c.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at) VALUES (?,?,'FR',?)", (st, bank, at))
    c.commit()
    c.execute(stmts[-1])
    got = dict(c.execute("SELECT state, completed_at FROM pending_auth"))
    assert got["fo-state"] == "2026-10-04T09:22:11+00:00" and got["ce-state"] is None and got["fo-later"] is None


# ---------------------------------------------------------------- N2 / N3

def test_n2_completion_advises_plain_sync():
    out = []
    auth.print_completion({"session_id": "s", "bank": "B", "valid_until": "x", "accounts": [], "retired": [],
                           "orphans": [], "other_active_sessions": []}, out.append)
    text = "\n".join(out)
    assert "uv run coach sync" in text and "--full" not in text


def test_n3_localhost_also_probes_ipv6(tmp_path):
    try:
        l6 = socket.socket(socket.AF_INET6)
        l6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        l6.bind(("::1", 0))
    except OSError:
        pytest.skip("no IPv6 loopback")
    l6.listen()
    port = l6.getsockname()[1]
    try:
        with pytest.raises(cb.CallbackError, match="localhost:%d is already served" % port):
            cb.CallbackServer(f"https://localhost:{port}/callback", lambda p: None, tls_dir=tmp_path / "t").bind()
        srv = cb.CallbackServer(f"https://127.0.0.1:{port}/callback", lambda p: None, tls_dir=tmp_path / "t")
        srv.bind()                         # an explicit 127.0.0.1 redirect does not care about ::1
        srv.close()
    finally:
        l6.close()
