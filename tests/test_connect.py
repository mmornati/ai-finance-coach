import dataclasses
import socket
import ssl
import stat
import threading
import time
from argparse import Namespace

import pytest
import requests

from coach.db import connect
from coach.ingest import accounts as acc, auth, callback as cb, commands as ic
from coach.ingest.sync import sync_all
from helpers import FakeClient, add_bank, add_tx, eb_tx, make_prototype_db, session_payload

IBAN_A, IBAN_B = "FR7600000000000000000000001", "FR7600000000000000000000002"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def con(cfg):
    return connect(cfg, insecure=True, create=True)


@pytest.fixture
def eb_cfg(cfg, tmp_path):
    key = tmp_path / "k.pem"
    key.write_text("x")
    return dataclasses.replace(cfg, eb_app_id="app", eb_private_key_path=str(key),
                               eb_redirect_url=f"https://127.0.0.1:{free_port()}/callback")


# ------------------------------------------------------------ reconnect: account-uid remapping

def test_reconnect_maps_new_account_uids_to_existing_records_and_retires_old_session(con):
    add_bank(con, "s-old", "Bank", "FR", [("uid-A1", IBAN_A, "CHECKING"), ("uid-B1", IBAN_B, "SAVINGS")])
    add_tx(con, "uid-A1", "uid-A1:ref:1", "2026-09-01", -10.0, "OLD HISTORY")
    acc.set_account(con, "uid-A1", label="Main", owner="joint", purpose="main")
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id) "
                "VALUES ('st1','Bank','FR','t','s-old')")
    con.commit()
    new = session_payload("s-new", "Bank", "FR", [("uid-A2", IBAN_A.lower(), "CHECKING"),   # new uids, same IBANs
                                                  ("uid-B2", IBAN_B, "SAVINGS")])
    fc = FakeClient(codes={"CODE": new})
    info = auth.complete_auth(con, fc, "CODE", "st1")
    assert [a["remapped"] for a in info["accounts"]] == [True, True]
    assert [a["uid"] for a in info["accounts"]] == ["uid-A1", "uid-B1"]          # stable ids kept
    assert con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2        # no duplicate records
    row = con.execute("SELECT session_id, api_uid, label, owner, purpose FROM accounts WHERE uid='uid-A1'").fetchone()
    assert row == ("s-new", "uid-A2", "Main", "joint", "main")                     # metadata survives
    assert con.execute("SELECT status, replaced_by FROM sessions WHERE session_id='s-old'").fetchone() == ("replaced", "s-new")
    assert con.execute("SELECT status FROM sessions WHERE session_id='s-new'").fetchone()[0] == "active"
    assert info["retired"] == ["s-old"]
    assert con.execute("SELECT completed_at FROM pending_auth WHERE state='st1'").fetchone()[0]
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1    # history untouched


def test_sync_after_reconnect_uses_new_bank_uid_but_keeps_history_and_dedups(con):
    add_bank(con, "s-old", "Bank", "FR", [("uid-A1", IBAN_A, "CHECKING")])
    page = [{"transactions": [eb_tx("r1", "2026-09-01", -10.0, "CARTE 01/09 SHOP")]}]
    fc = FakeClient({"uid-A1": page})
    sync_all(con, fc, out=lambda *_: None)
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id) "
                "VALUES ('st','Bank','FR','t','s-old')")
    con.commit()
    auth.complete_auth(con, FakeClient(codes={"C": session_payload("s-new", "Bank", "FR", [("uid-A2", IBAN_A, "CHECKING")])}),
                       "C", "st")
    fc2 = FakeClient({"uid-A2": [{"transactions": [eb_tx("r1", "2026-09-01", -10.0, "CARTE 01/09 SHOP"),
                                                   eb_tx("r2", "2026-09-05", -3.0, "CARTE 05/09 CAFE")]}]})
    res = sync_all(con, fc2, force=True, out=lambda *_: None)
    assert [r["status"] for r in res] == ["ok"] and res[0]["new"] == 1             # r1 not duplicated
    assert all("/accounts/uid-A1" not in c[1] for c in fc2.calls) and any("/accounts/uid-A2/transactions" in c[1] for c in fc2.calls)
    assert con.execute("SELECT COUNT(*) FROM transactions WHERE account_uid='uid-A1'").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM transactions WHERE account_uid='uid-A2'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM sync_log WHERE account_uid='uid-A1'").fetchone()[0] == 2


def test_reconnect_with_unmatched_account_keeps_it_on_retired_session_and_reports(con):
    add_bank(con, "s-old", "Bank", "FR", [("uid-A1", IBAN_A, "CHK"), ("uid-B1", IBAN_B, "SAV")])
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id) "
                "VALUES ('st','Bank','FR','t','s-old')")
    con.commit()
    info = auth.complete_auth(con, FakeClient(codes={"C": session_payload(
        "s-new", "Bank", "FR", [("uid-A2", IBAN_A, "CHK"), ("uid-C2", "FR7600000000000000000000009", "NEW")])}), "C", "st")
    assert info["orphans"] == ["uid-B1"] and [a["new"] for a in info["accounts"]] == [False, True]
    assert con.execute("SELECT session_id FROM accounts WHERE uid='uid-B1'").fetchone()[0] == "s-old"
    out = []
    auth.print_completion(info, out.append)
    assert any("not returned by the new session" in l for l in out)
    # the orphan is not synced any more (its session is replaced)
    fc = FakeClient({"uid-A1": [{"transactions": []}], "uid-C2": [{"transactions": []}]})
    assert {r["uid"] for r in sync_all(con, fc, out=lambda *_: None)} == {"uid-A1", "uid-C2"}   # not uid-B1


def test_completion_without_replaces_still_maps_by_iban_and_retires_emptied_session(con):
    add_bank(con, "s-old", "Bank", "FR", [("uid-A1", IBAN_A, "CHK")], "2020-01-01T00:00:00+00:00")   # expired
    info = auth.complete_auth(con, FakeClient(codes={"C": session_payload("s-new", "Bank", "FR", [("uid-A2", IBAN_A, "CHK")])}), "C")
    assert info["accounts"][0]["uid"] == "uid-A1" and info["retired"] == ["s-old"]


def test_same_uid_refinish_is_idempotent(con):
    p = session_payload("s1", "Bank", "FR", [("u1", IBAN_A, "CHK")])
    auth.complete_auth(con, FakeClient(codes={"C": p}), "C")
    info = auth.complete_auth(con, FakeClient(codes={"C": p}), "C")
    assert con.execute("SELECT COUNT(*), MAX(api_uid) FROM accounts").fetchone() == (1, None)
    assert not info["accounts"][0]["remapped"]


# ------------------------------------------------------------ one consent owner per bank

def test_second_consent_for_same_bank_is_refused_without_replace(con):
    add_bank(con, "s1", "Bank", "FR", [("u1", IBAN_A, "CHK")])
    with pytest.raises(auth.ConnectError) as e:
        auth.check_single_consent(con, "bank", "fr", replace=False)                 # case-insensitive
    msg = str(e.value)
    assert "--replace" in msg and "BankMCP" in msg and "coach reconnect" in msg
    auth.check_single_consent(con, "bank", "FR", replace=True)                      # allowed with --replace
    auth.check_single_consent(con, "Bank", "IT", replace=False)                     # other country: independent
    auth.check_single_consent(con, "Other", "FR", replace=False)


def test_dead_consent_does_not_block_a_new_connect(con):
    add_bank(con, "s1", "Bank", "FR", [("u1", IBAN_A, "CHK")], "2020-01-01T00:00:00+00:00")
    auth.check_single_consent(con, "Bank", "FR", replace=False)


def test_cmd_connect_guard_makes_no_bank_call(eb_cfg, monkeypatch):
    c = connect(eb_cfg, insecure=True, create=True)
    add_bank(c, "s1", "Bank", "FR", [("u1", IBAN_A, "CHK")])
    c.close()
    calls = []
    monkeypatch.setattr(ic.EnableBankingClient, "call", lambda self, *a, **k: calls.append(a) or {"url": "https://bank/auth"})
    with pytest.raises(SystemExit, match="already has an active consent"):
        ic.cmd_connect(Namespace(insecure=True, bank="Bank", country="FR", days=90, no_browser=True,
                                 no_server=True, replace=False, timeout=None), eb_cfg)
    assert calls == []
    ic.cmd_connect(Namespace(insecure=True, bank="Bank", country="FR", days=90, no_browser=True,
                             no_server=True, replace=True, timeout=None), eb_cfg)
    assert calls[0][:2] == ("POST", "/auth")
    c = connect(eb_cfg, insecure=True)
    assert c.execute("SELECT replaces_session_id FROM pending_auth").fetchone()[0] == "s1"


def test_reconnect_targets_by_name_or_id_and_starts_same_aspsp(eb_cfg, monkeypatch):
    c = connect(eb_cfg, insecure=True, create=True)
    add_bank(c, "s1", "Fortuneo", "FR", [("u1", IBAN_A, "CHK")], "2020-01-01T00:00:00+00:00")
    add_bank(c, "s2", "Revolut", "LT", [("u2", IBAN_B, "KIDS")])
    c.close()
    sent = []
    monkeypatch.setattr(ic.EnableBankingClient, "call",
                        lambda self, m, p, **k: sent.append(k["json"]) or {"url": "https://bank/auth"})
    ic.cmd_reconnect(Namespace(insecure=True, target="fortu", days=60, no_browser=True, no_server=True,
                               timeout=None), eb_cfg)
    assert sent[0]["aspsp"] == {"name": "Fortuneo", "country": "FR"}
    ic.cmd_reconnect(Namespace(insecure=True, target="s2", days=60, no_browser=True, no_server=True,
                               timeout=None), eb_cfg)
    assert sent[1]["aspsp"] == {"name": "Revolut", "country": "LT"}
    with pytest.raises(SystemExit, match="No bank session"):
        ic.cmd_reconnect(Namespace(insecure=True, target="nope", days=60, no_browser=True, no_server=True,
                                   timeout=None), eb_cfg)
    pa = connect(eb_cfg, insecure=True).execute("SELECT aspsp_name, replaces_session_id FROM pending_auth ORDER BY created_at").fetchall()
    assert sorted(pa) == [("Fortuneo", "s1"), ("Revolut", "s2")]


# ------------------------------------------------------------ finish keeps working (incl. prototype pending_auth rows)

def test_finish_works_for_existing_pending_auth_rows_and_refuses_reuse(cfg, monkeypatch):
    cfg.db_path.parent.mkdir(parents=True)
    make_prototype_db(cfg.db_path)
    fc = FakeClient(codes={"CODE1": session_payload("s-new", "Test Bank", "FR", [("acc-new", "FR7600000000000000009999", "NEW")])})
    monkeypatch.setattr(ic.EnableBankingClient, "from_config", classmethod(lambda cls, c: fc))
    ic.cmd_finish(Namespace(insecure=True, code_or_url="https://localhost:8443/callback?state=state-aaa&code=CODE1"), cfg)
    c = connect(cfg, insecure=True)
    assert c.execute("SELECT uid FROM accounts WHERE uid='acc-new'").fetchone()
    assert c.execute("SELECT completed_at IS NOT NULL FROM pending_auth WHERE state='state-aaa'").fetchone()[0]
    assert c.execute("SELECT completed_at FROM pending_auth WHERE state='state-bbb'").fetchone()[0] is None
    with pytest.raises(SystemExit, match="already completed"):
        ic.cmd_finish(Namespace(insecure=True, code_or_url="https://localhost:8443/callback?state=state-aaa&code=CODE1"), cfg)
    with pytest.raises(SystemExit, match="Unknown state"):
        ic.cmd_finish(Namespace(insecure=True, code_or_url="https://localhost:8443/callback?state=zzz&code=C"), cfg)


def test_finish_with_bare_code_still_works(con, cfg, monkeypatch):
    fc = FakeClient(codes={"BARE": session_payload("s9", "Bank", "FR", [("u9", IBAN_A, "CHK")])})
    monkeypatch.setattr(ic.EnableBankingClient, "from_config", classmethod(lambda cls, c: fc))
    ic.cmd_finish(Namespace(insecure=True, code_or_url="BARE"), cfg)
    assert connect(cfg, insecure=True).execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


# ------------------------------------------------------------ TLS certificate

def test_cert_generated_once_private_key_0600_and_reused(tmp_path):
    d = tmp_path / "tls"
    cert, key = cb.ensure_cert(d)
    assert stat.S_IMODE(key.stat().st_mode) == 0o600 and stat.S_IMODE(d.stat().st_mode) == 0o700
    before = cert.read_bytes()
    assert cb.ensure_cert(d) == (cert, key) and cert.read_bytes() == before        # reused
    from cryptography import x509
    c = x509.load_pem_x509_certificate(before)
    san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    assert {str(i) for i in san.get_values_for_type(x509.IPAddress)} == {"127.0.0.1", "::1"}
    key.write_text("garbage")                                                       # broken pair is regenerated
    cert2, key2 = cb.ensure_cert(d)
    assert cert2.read_bytes() != before and stat.S_IMODE(key2.stat().st_mode) == 0o600


def test_redirect_must_be_loopback():
    assert cb.parse_redirect("https://localhost:8443/callback") == ("https", "127.0.0.1", 8443, "/callback")
    assert cb.parse_redirect("https://[::1]:9000/x")[1] == "::1"
    for bad in ("https://example.com/cb", "ftp://localhost/x", "https://0.0.0.0:8443/cb"):
        with pytest.raises(cb.CallbackError):
            cb.parse_redirect(bad)


# ------------------------------------------------------------ callback server end to end

def run_flow_in_thread(cfg, fc, timeout=10, **kw):
    """connect_flow in a worker thread (own DB connection, as in the CLI); returns (thread, box, ready)."""
    box, ready = {}, threading.Event()

    def work():
        con = connect(cfg, insecure=True)
        box["out"] = []
        try:
            box["code"] = auth.connect_flow(
                con, fc, cfg, "Bank", "FR", 90, replaces=kw.get("replaces"), no_browser=True, timeout=timeout,
                out=box["out"].append, on_listening=lambda port, state: (box.update(port=port, state=state), ready.set()))
        except Exception as e:
            box["exc"] = e
            ready.set()

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t, box, ready


def get(cfg, box, path, **params):
    url = f"https://127.0.0.1:{box['port']}{path}"
    return requests.get(url, params=params, verify=str(cfg.tls_dir / cb.CERT_NAME), timeout=5)


def test_callback_server_completes_session_end_to_end(eb_cfg):
    c = connect(eb_cfg, insecure=True, create=True)
    add_bank(c, "s-old", "Bank", "FR", [("uid-A1", IBAN_A, "CHK")])
    c.close()
    fc = FakeClient(codes={"THECODE": session_payload("s-new", "Bank", "FR", [("uid-A2", IBAN_A, "CHK")])})
    t, box, ready = run_flow_in_thread(eb_cfg, fc, replaces="s-old")
    assert ready.wait(10) and "exc" not in box
    r = get(eb_cfg, box, "/callback", state=box["state"], code="THECODE")      # cert verified against our own file
    assert r.status_code == 200 and "Bank connected" in r.text and "THECODE" not in r.text
    assert r.headers["Cache-Control"] == "no-store"
    t.join(10)
    assert not t.is_alive() and box["code"] == 0
    con = connect(eb_cfg, insecure=True)
    assert con.execute("SELECT status FROM sessions WHERE session_id='s-old'").fetchone()[0] == "replaced"
    assert con.execute("SELECT api_uid FROM accounts WHERE uid='uid-A1'").fetchone()[0] == "uid-A2"
    assert con.execute("SELECT completed_at IS NOT NULL FROM pending_auth WHERE state=?", (box["state"],)).fetchone()[0]
    assert any("Retired replaced session" in l for l in box["out"])
    with pytest.raises(requests.ConnectionError):                                 # server shut down
        get(eb_cfg, box, "/callback", state=box["state"], code="THECODE")


def test_callback_validates_state_and_keeps_waiting(eb_cfg):
    connect(eb_cfg, insecure=True, create=True).close()
    fc = FakeClient(codes={"C": session_payload("s1", "Bank", "FR", [("u1", IBAN_A, "CHK")])})
    t, box, ready = run_flow_in_thread(eb_cfg, fc)
    assert ready.wait(10)
    bad = get(eb_cfg, box, "/callback", state="not-the-state", code="C")
    assert bad.status_code == 400
    assert get(eb_cfg, box, "/callback", code="C").status_code == 400             # no state at all
    assert get(eb_cfg, box, "/elsewhere", state=box["state"], code="C").status_code == 404
    assert t.is_alive()                                                            # still waiting
    assert connect(eb_cfg, insecure=True).execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert get(eb_cfg, box, "/callback", state=box["state"], code="C").status_code == 200
    t.join(10)
    assert box["code"] == 0


def test_callback_error_param_fails_without_connecting(eb_cfg):
    connect(eb_cfg, insecure=True, create=True).close()
    t, box, ready = run_flow_in_thread(eb_cfg, FakeClient())
    assert ready.wait(10)
    r = get(eb_cfg, box, "/callback", state=box["state"], error="access_denied")
    assert r.status_code == 400 and "Connection failed" in r.text
    t.join(10)
    assert box["code"] == 1 and connect(eb_cfg, insecure=True).execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_callback_bank_exchange_failure_is_reported(eb_cfg):
    connect(eb_cfg, insecure=True, create=True).close()
    t, box, ready = run_flow_in_thread(eb_cfg, FakeClient(codes={}))               # POST /sessions raises KeyError
    assert ready.wait(10)
    r = get(eb_cfg, box, "/callback", state=box["state"], code="WRONG")
    assert r.status_code == 500
    t.join(10)
    assert box["code"] == 1 and any("Failed" in l for l in box["out"])


def test_callback_timeout_leaves_pending_auth_for_finish(eb_cfg):
    connect(eb_cfg, insecure=True, create=True).close()
    t, box, ready = run_flow_in_thread(eb_cfg, FakeClient(), timeout=0.6)
    t.join(10)
    assert box["code"] == 1 and any("Timed out" in l and "coach finish" in l for l in box["out"])
    row = connect(eb_cfg, insecure=True).execute("SELECT completed_at FROM pending_auth").fetchall()
    assert row == [(None,)]                                                        # still finishable by hand


def test_port_in_use_is_a_clear_error_before_calling_the_bank(eb_cfg):
    connect(eb_cfg, insecure=True, create=True).close()
    port = int(eb_cfg.eb_redirect_url.split(":")[2].split("/")[0])
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen()
    try:
        fc = FakeClient()
        with pytest.raises(auth.ConnectError, match="--no-server"):
            auth.connect_flow(connect(eb_cfg, insecure=True), fc, eb_cfg, "Bank", "FR", 90, out=lambda *_: None)
        assert fc.calls == []                                                      # /auth never called
    finally:
        blocker.close()


def test_no_server_flag_keeps_manual_flow_and_opens_no_socket(eb_cfg):
    c = connect(eb_cfg, insecure=True, create=True)
    opened, out = [], []
    code = auth.connect_flow(c, FakeClient(), eb_cfg, "Bank", "FR", 90, no_server=True, no_browser=False,
                             open_browser=opened.append, out=out.append)
    assert code == 0 and opened and any("coach finish" in l for l in out)
    port = int(eb_cfg.eb_redirect_url.split(":")[2].split("/")[0])
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.5)


def test_non_loopback_redirect_falls_back_to_manual_flow(cfg, tmp_path):
    cfg = dataclasses.replace(cfg, eb_redirect_url="https://example.org/cb")
    c = connect(cfg, insecure=True, create=True)
    out = []
    assert auth.connect_flow(c, FakeClient(), cfg, "Bank", "FR", 90, no_browser=True, out=out.append) == 0
    assert any("falling back to the manual flow" in l for l in out)


def test_tls_handshake_noise_does_not_stop_the_server(eb_cfg):
    """A browser that rejects the self-signed cert aborts the handshake; the server keeps waiting."""
    connect(eb_cfg, insecure=True, create=True).close()
    fc = FakeClient(codes={"C": session_payload("s1", "Bank", "FR", [("u1", IBAN_A, "CHK")])})
    t, box, ready = run_flow_in_thread(eb_cfg, fc)
    assert ready.wait(10)
    s = socket.create_connection(("127.0.0.1", box["port"]), timeout=2)
    s.sendall(b"GET / HTTP/1.1\r\n\r\n")          # plain HTTP to a TLS port
    s.close()
    time.sleep(0.4)
    assert t.is_alive()
    assert get(eb_cfg, box, "/callback", state=box["state"], code="C").status_code == 200
    t.join(10)


# ------------------------------------------------------------ review round 2

from coach.ingest.accounts import merge_accounts


def pocket_session(sid, *pockets, bank="Revolut", country="LT", iban="LT000000000000000001"):
    """One IBAN, several currency pockets: pockets = [(uid, currency)]"""
    return {"session_id": sid, "aspsp": {"name": bank, "country": country},
            "access": {"valid_until": "2099-01-01T00:00:00Z"},
            "accounts": [{"uid": u, "name": "Main", "currency": cur, "cash_account_type": "CACC",
                          "account_id": {"iban": iban}} for u, cur in pockets]}


def test_item1_same_iban_currency_pockets_stay_separate_on_first_connect(con):
    info = auth.complete_auth(con, FakeClient(codes={"C": pocket_session("s1", ("u-eur", "EUR"), ("u-usd", "USD"))}), "C")
    assert [a["uid"] for a in info["accounts"]] == ["u-eur", "u-usd"]
    assert con.execute("SELECT uid, currency, api_uid FROM accounts ORDER BY uid").fetchall() == [
        ("u-eur", "EUR", None), ("u-usd", "USD", None)]


@pytest.mark.parametrize("order", ["same", "swapped"])
def test_item1_reconnect_maps_pockets_one_to_one_by_currency(con, order):
    auth.complete_auth(con, FakeClient(codes={"C": pocket_session("s-old", ("u-eur", "EUR"), ("u-usd", "USD"))}), "C")
    add_tx(con, "u-eur", "u-eur:ref:1", "2026-09-01", -5.0, "EUR HISTORY")
    add_tx(con, "u-usd", "u-usd:ref:1", "2026-09-01", -7.0, "USD HISTORY")
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id) "
                "VALUES ('st','Revolut','LT','t','s-old')")
    con.commit()
    pockets = [("n-eur", "EUR"), ("n-usd", "USD")]
    if order == "swapped":
        pockets.reverse()
    info = auth.complete_auth(con, FakeClient(codes={"C2": pocket_session("s-new", *pockets)}), "C2", "st")
    assert con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2                 # nothing merged or lost
    assert con.execute("SELECT uid, currency, api_uid, session_id, needs_review FROM accounts ORDER BY uid").fetchall() == [
        ("u-eur", "EUR", "n-eur", "s-new", 0), ("u-usd", "USD", "n-usd", "s-new", 0)]
    assert {a["uid"] for a in info["accounts"]} == {"u-eur", "u-usd"} and info["orphans"] == []
    assert con.execute("SELECT account_uid FROM transactions WHERE description='USD HISTORY'").fetchone()[0] == "u-usd"


def test_item1_remap_only_onto_replaced_or_dead_sessions_of_the_same_bank(con):
    add_bank(con, "s-live", "Other Bank", "FR", [("o1", IBAN_A, "CHK")])               # same IBAN, other bank, live
    info = auth.complete_auth(con, FakeClient(codes={"C": session_payload("s-new", "Bank", "FR", [("n1", IBAN_A, "CHK")])}), "C")
    assert info["accounts"][0]["new"] and con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2
    add_bank(con, "s-live2", "Bank", "FR", [("x1", IBAN_B, "CHK")])                    # same bank but a LIVE consent
    info = auth.complete_auth(con, FakeClient(codes={"C2": session_payload("s-new2", "Bank", "FR", [("x2", IBAN_B, "CHK")])}), "C2")
    assert info["accounts"][0]["new"] and info["retired"] == []
    assert con.execute("SELECT session_id FROM accounts WHERE uid='x1'").fetchone()[0] == "s-live2"


def test_item1_each_old_account_is_claimed_once(con):
    auth.complete_auth(con, FakeClient(codes={"C": pocket_session("s-old", ("u-eur", "EUR"))}), "C")
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id) "
                "VALUES ('st','Revolut','LT','t','s-old')")
    con.commit()
    # the new session returns TWO EUR accounts on that IBAN: only one can inherit the history
    info = auth.complete_auth(con, FakeClient(codes={"C2": pocket_session("s-new", ("n1", "EUR"), ("n2", "EUR"))}), "C2", "st")
    assert [(a["uid"], a["new"]) for a in info["accounts"]] == [("u-eur", False), ("n2", True)]   # claimed once
    assert con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2


# ---- item 3: accounts without IBAN

def card_session(sid, uid, name="VISA PREMIER", typ="CARD"):
    return {"session_id": sid, "aspsp": {"name": "CardBank", "country": "FR"},
            "access": {"valid_until": "2099-01-01T00:00:00Z"},
            "accounts": [{"uid": uid, "name": name, "currency": "EUR", "cash_account_type": typ, "account_id": {}}]}


def test_item3_card_without_iban_is_matched_by_bank_product_currency_type(con):
    auth.complete_auth(con, FakeClient(codes={"C": card_session("s-old", "c1")}), "C")
    add_tx(con, "c1", "c1:ref:1", "2026-09-01", -5.0, "H")
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id) "
                "VALUES ('st','CardBank','FR','t','s-old')")
    con.commit()
    info = auth.complete_auth(con, FakeClient(codes={"C2": card_session("s-new", "c2")}), "C2", "st")
    assert info["accounts"][0]["uid"] == "c1" and info["accounts"][0]["remapped"]
    assert con.execute("SELECT COUNT(*), MAX(api_uid) FROM accounts").fetchone() == (1, "c2")
    # a different product name or type is NOT the same card
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id) "
                "VALUES ('st2','CardBank','FR','t','s-new')")
    con.commit()
    info = auth.complete_auth(con, FakeClient(codes={"C3": card_session("s-new2", "c3", name="OTHER CARD")}), "C3", "st2")
    assert info["accounts"][0]["new"] and not info["accounts"][0]["needs_review"]


def two_cards(sid, a, b):
    s = card_session(sid, a)
    s["accounts"].append({**s["accounts"][0], "uid": b})
    return s


def test_item3_ambiguous_match_flags_needs_review_and_excludes_until_resolved(con, cfg):
    from coach.classify.rules import categorised
    auth.complete_auth(con, FakeClient(codes={"C": two_cards("s-old", "c1", "c2")}), "C")     # identical twin cards
    add_tx(con, "c1", "c1:ref:1", "2026-09-01", -5.0, "H1", "card")
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id) "
                "VALUES ('st','CardBank','FR','t','s-old')")
    con.commit()
    info = auth.complete_auth(con, FakeClient(codes={"C2": card_session("s-new", "c9")}), "C2", "st")
    a = info["accounts"][0]
    assert a["new"] and a["needs_review"] and sorted(a["candidates"]) == ["c1", "c2"]
    assert con.execute("SELECT uid FROM accounts WHERE needs_review=1 ORDER BY uid").fetchall() == [("c1",), ("c2",), ("c9",)]
    out = []
    auth.print_completion(info, out.append)
    assert any("NEEDS REVIEW" in l for l in out)
    assert list(categorised(con, rules={"type_rules": {}, "merchant_rules": []}, annotations=[])) == []   # left out of analytics
    fc = FakeClient({"c9": [{"transactions": []}]})
    assert sync_all(con, fc, out=lambda *_: None) == []                                     # and not synced
    from coach.ingest.accounts import AccountError
    with pytest.raises(AccountError, match="needs review"):
        sync_all(con, fc, "c9", out=lambda *_: None)
    from coach.ingest import health as hm
    assert any("needs review" in p for b in hm.health(con).banks for x in b.accounts for p in x.problems)
    # manual resolution: merge the new account into the right old one
    con.execute("INSERT INTO transactions(tx_key, account_uid, booking_date, amount, currency, description) "
                "VALUES ('c9:ref:1','c9','2026-09-01',-5,'EUR','H1')")
    con.execute("INSERT INTO transactions(tx_key, account_uid, booking_date, amount, currency, description) "
                "VALUES ('c9:ref:2','c9','2026-09-02',-6,'EUR','H2')")
    add_tx(con, "c1", "c1:ref:2", "2026-09-02", -6.0, "H2")                                  # the same bank row, old key
    merge_accounts(con, "c1", "c9")
    assert con.execute("SELECT COUNT(*) FROM accounts WHERE uid='c9'").fetchone()[0] == 0
    assert con.execute("SELECT session_id, api_uid FROM accounts WHERE uid='c1'").fetchone() == ("s-new", "c9")
    assert con.execute("SELECT COUNT(*) FROM transactions WHERE account_uid='c1'").fetchone()[0] == 2   # no duplicates
    from coach.ingest.accounts import set_account
    set_account(con, "c2", resolve=True)
    assert con.execute("SELECT COUNT(*) FROM accounts WHERE needs_review=1").fetchone()[0] == 0


def test_item3_cli_merge_and_resolve(cfg, con, capsys):
    from coach.cli import main
    auth.complete_auth(con, FakeClient(codes={"C": two_cards("s-old", "c1", "c2")}), "C")
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id) "
                "VALUES ('st','CardBank','FR','t','s-old')")
    con.commit()
    auth.complete_auth(con, FakeClient(codes={"C2": card_session("s-new", "c9")}), "C2", "st")
    con.close()
    base = ["--config", str(cfg.config_path), "--insecure"]
    main(base + ["accounts"])
    assert "NEEDS REVIEW" in capsys.readouterr().out
    main(base + ["accounts", "merge", "c1", "c9"])
    assert "merged c9 into c1" in capsys.readouterr().out
    main(base + ["accounts", "set", "c2", "--resolve"])
    with pytest.raises(SystemExit, match="same account"):
        main(base + ["accounts", "merge", "c1", "c1"])


# ---- item 12: atomic completion

def test_item12_only_one_completer_proceeds(cfg, con):
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at) VALUES ('st','Bank','FR','t')")
    con.commit()
    other = connect(cfg, insecure=True)
    box = {}

    class Racing(FakeClient):
        def call(self, method, path, **kw):
            if path == "/sessions" and "second" not in box:        # while the first completer is at the bank...
                box["second"] = True
                try:
                    auth.complete_auth(other, FakeClient(codes={"C": session_payload("s9", "Bank", "FR", [("u9", IBAN_A, "X")])}), "C", "st")
                except auth.ConnectError as e:
                    box["err"] = str(e)
            return super().call(method, path, **kw)

    info = auth.complete_auth(con, Racing(codes={"C": session_payload("s1", "Bank", "FR", [("u1", IBAN_A, "X")])}), "C", "st")
    assert "already being completed" in box["err"] or "already completed" in box["err"]
    assert info["session_id"] == "s1" and con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_item12_claim_is_released_when_the_bank_call_fails(con):
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at) VALUES ('st','Bank','FR','t')")
    con.commit()
    with pytest.raises(KeyError):
        auth.complete_auth(con, FakeClient(codes={}), "BADCODE", "st")
    assert con.execute("SELECT completed_at FROM pending_auth").fetchone()[0] is None
    auth.complete_auth(con, FakeClient(codes={"C": session_payload("s1", "Bank", "FR", [("u1", IBAN_A, "X")])}), "C", "st")
    assert con.execute("SELECT completed_at IS NOT NULL FROM pending_auth").fetchone()[0]


# ---- items 6 and 7: callback server hardening

def _tls_client_ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def test_item6_slow_loris_cannot_outlive_the_server_timeout(tmp_path):
    port = free_port()
    srv = cb.CallbackServer(f"https://127.0.0.1:{port}/callback", lambda p: cb.Outcome(True, True, "x"),
                            tls_dir=tmp_path / "tls", timeout=1.5)
    srv.bind()
    box = {}

    def serve():
        t0 = time.monotonic()
        box["res"] = srv.serve()
        box["elapsed"] = time.monotonic() - t0

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    stop = threading.Event()

    def loris():
        try:
            s = _tls_client_ctx().wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=3))
            s.send(b"GET /callback?state=x HTTP/1.1\r\n")
            while not stop.is_set():
                s.send(b"X-a: b")        # dribble, never finishing the headers
                time.sleep(0.2)
        except OSError:
            pass

    threading.Thread(target=loris, daemon=True).start()
    t.join(15)
    stop.set()
    assert not t.is_alive(), "server is stuck on a dribbling connection"
    assert box["res"].status == "timeout" and box["elapsed"] < 6.0
    srv.close()


def test_item6_silent_tcp_connection_cannot_outlive_the_timeout(tmp_path):
    port = free_port()
    srv = cb.CallbackServer(f"https://127.0.0.1:{port}/callback", lambda p: cb.Outcome(True, True, "x"),
                            tls_dir=tmp_path / "tls", timeout=1.0)
    srv.bind()
    idle = socket.create_connection(("127.0.0.1", port), timeout=3)          # connects, never speaks TLS
    t0 = time.monotonic()
    assert srv.serve().status == "timeout" and time.monotonic() - t0 < 6.0
    idle.close()
    srv.close()


def test_item7_port_already_served_by_a_wildcard_listener_is_refused(tmp_path):
    other = socket.socket()
    other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    other.bind(("0.0.0.0", 0))
    other.listen()
    port = other.getsockname()[1]
    try:
        srv = cb.CallbackServer(f"https://127.0.0.1:{port}/callback", lambda p: None, tls_dir=tmp_path / "tls")
        with pytest.raises(cb.CallbackError, match="already served by another program"):
            srv.bind()
    finally:
        other.close()
    srv = cb.CallbackServer(f"https://127.0.0.1:{port}/callback", lambda p: None, tls_dir=tmp_path / "tls")
    srv.bind()                                                                  # free again: binds fine
    srv.close()
