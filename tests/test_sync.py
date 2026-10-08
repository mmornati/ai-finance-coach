import json
from datetime import date, datetime, timezone

import pytest

from coach.db import connect
from coach.ingest import client as client_mod
from coach.ingest.client import ApiError, EnableBankingClient
from coach.ingest.sync import sync_account, sync_all, tx_key
from helpers import FakeClient, eb_tx


@pytest.fixture
def con(cfg):
    c = connect(cfg, insecure=True, create=True)
    c.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw) VALUES ('s1','Test Bank','FR','2099-01-01','t','{}')")
    c.execute("INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw) VALUES ('acc1','s1','ACC','FR76','EUR','CACC','{}')")
    c.commit()
    return c


def two_pages():
    return [
        {"transactions": [eb_tx("r1", "2025-10-01", -5.0, "CARTE 30/09 A"),
                          eb_tx("r2", "2025-10-02", -6.0, "CARTE 01/10 B"),
                          eb_tx("p1", "2025-10-03", -7.0, "CARTE 02/10 C", status="PDNG")],
         "continuation_key": "K1"},
        {"transactions": [eb_tx("r3", "2025-10-04", 20.0, "VIR X", debit=False)]},
    ]


def test_first_sync_uses_longest_and_keeps_params_with_continuation_key(con):
    fc = FakeClient({"acc1": two_pages()})
    res = sync_account(con, fc, "acc1", full=False, force=False, out=lambda *_: None)
    assert res["status"] == "ok" and res["new"] == 3 and res["pages"] == 2 and res["pending"] == 1
    tx_calls = [c for c in fc.calls if c[1].endswith("/transactions")]
    assert tx_calls[0][2] == {"strategy": "longest"}
    # second page keeps the ORIGINAL params and adds the continuation key
    assert tx_calls[1][2] == {"strategy": "longest", "continuation_key": "K1"}
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3
    # signs: DBIT negative, CRDT positive
    assert con.execute("SELECT amount FROM transactions WHERE entry_reference='r3'").fetchone()[0] == 20.0
    assert con.execute("SELECT amount FROM transactions WHERE entry_reference='r1'").fetchone()[0] == -5.0
    log = con.execute("SELECT ok, new_tx, pages FROM sync_log").fetchone()
    assert log == (1, 3, 2)


def test_incremental_sync_overlaps_a_week_and_dedups(con):
    fc = FakeClient({"acc1": two_pages() + two_pages()})
    sync_account(con, fc, "acc1", False, False, out=lambda *_: None)
    res = sync_account(con, fc, "acc1", False, True, out=lambda *_: None)
    assert res["new"] == 0                                    # re-sync adds nothing
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 3
    second_first_call = [c for c in fc.calls if c[1].endswith("/transactions")][2]
    assert second_first_call[2] == {"date_from": "2025-09-27"}  # last booking 2025-10-04 - 7 days
    # last_seen updated, first_seen kept
    fs, ls = con.execute("SELECT first_seen, last_seen FROM transactions WHERE entry_reference='r1'").fetchone()
    assert fs <= ls


def test_full_flag_forces_longest_again(con):
    fc = FakeClient({"acc1": two_pages() + two_pages()})
    sync_account(con, fc, "acc1", False, False, out=lambda *_: None)
    sync_account(con, fc, "acc1", True, True, out=lambda *_: None)
    assert [c for c in fc.calls if c[1].endswith("/transactions")][2][2] == {"strategy": "longest"}


def test_pending_transactions_are_replaced_each_sync_and_booked_version_wins(con):
    first = [{"transactions": [eb_tx("p1", "2025-10-03", -7.0, "CARTE 02/10 C", status="PDNG"),
                               eb_tx("p2", "2025-10-03", -8.0, "CARTE 02/10 D", status="PDNG")]}]
    second = [{"transactions": [eb_tx("b1", "2025-10-03", -7.0, "CARTE 02/10 C"),   # p1 got booked
                                eb_tx("p3", "2025-10-04", -1.0, "CARTE 03/10 E", status="PDNG")]}]
    fc = FakeClient({"acc1": first + second})
    sync_account(con, fc, "acc1", False, False, out=lambda *_: None)
    assert con.execute("SELECT COUNT(*) FROM pending_transactions").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0     # pending never in history
    sync_account(con, fc, "acc1", False, True, out=lambda *_: None)
    pend = con.execute("SELECT description FROM pending_transactions").fetchall()
    assert pend == [("CARTE 03/10 E",)]                                              # replaced, not appended
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_rate_limit_429_rolls_back_and_is_logged(con):
    pages = [two_pages()[0], ApiError(429, "ASPSP_RATE_LIMIT_EXCEEDED")]
    fc = FakeClient({"acc1": pages})
    msgs = []
    res = sync_account(con, fc, "acc1", False, False, out=msgs.append)
    assert res["status"] == "failed" and res["http_status"] == 429
    assert "rate limited by bank" in msgs[-1]
    assert con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0     # page 1 rolled back
    ok, new, pgs, note = con.execute("SELECT ok, new_tx, pages, note FROM sync_log").fetchone()
    assert (ok, new, pgs) == (0, 0, 1) and "rate limited" in note


def test_other_api_error_is_logged_with_message(con):
    fc = FakeClient({"acc1": [ApiError(401, "expired")]})
    res = sync_account(con, fc, "acc1", False, False, out=lambda *_: None)
    assert res["status"] == "failed" and "HTTP 401" in res["note"]


def test_daily_limit_skips_without_calling_the_api(con):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for _ in range(4):
        con.execute("INSERT INTO sync_log VALUES ('acc1',?,1,0,1,'')", (now,))
    con.commit()
    fc = FakeClient({"acc1": two_pages()})
    assert sync_account(con, fc, "acc1", False, False, daily_limit=4, out=lambda *_: None)["status"] == "skipped"
    assert fc.calls == []
    # --force bypasses; a higher configured limit allows it too
    assert sync_account(con, fc, "acc1", False, True, daily_limit=4, out=lambda *_: None)["status"] == "ok"


def test_sync_all_runs_every_account(con):
    con.execute("INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw) VALUES ('acc2','s1','ACC2','FR77','EUR','CACC','{}')")
    con.commit()
    fc = FakeClient({"acc1": two_pages(), "acc2": [{"transactions": []}]})
    res = sync_all(con, fc, out=lambda *_: None)
    assert [r["uid"] for r in res] == ["acc1", "acc2"]


def test_balances_stored(con):
    fc = FakeClient({"acc1": [{"transactions": []}]}, balances={"balances": [
        {"balance_type": "CLBD", "balance_amount": {"amount": "100.50", "currency": "EUR"},
         "reference_date": "2025-10-04"}]})
    sync_account(con, fc, "acc1", False, False, out=lambda *_: None)
    assert con.execute("SELECT balance_type, amount, currency FROM balances").fetchone() == ("CLBD", 100.5, "EUR")


def test_tx_key_uses_reference_else_stable_fingerprint():
    a = eb_tx("r1", "2025-10-01", -5.0, "X")
    assert tx_key("acc1", a) == "acc1:ref:r1"
    b = eb_tx(None, "2025-10-01", -5.0, "X"); b["entry_reference"] = None
    k1, k2 = tx_key("acc1", b), tx_key("acc1", dict(b))
    assert k1 == k2 and k1.startswith("acc1:fp:")
    c = dict(b, remittance_information=["Y"])
    assert tx_key("acc1", c) != k1


# ---- HTTP layer (requests is mocked, no network)

class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload, text
        self.content = json.dumps(payload).encode() if payload is not None else b""

    def json(self):
        return self._p


@pytest.fixture
def rsa_key(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    p = tmp_path / "test-key.pem"
    p.write_bytes(pem)
    return p


def test_client_sends_jwt_and_maps_http_errors(monkeypatch, rsa_key):
    import jwt
    seen = {}

    def fake_request(method, url, headers=None, timeout=None, **kw):
        seen.update(method=method, url=url, headers=headers, kw=kw)
        return FakeResp(429, text="ASPSP_RATE_LIMIT_EXCEEDED")

    monkeypatch.setattr(client_mod.requests, "request", fake_request)
    c = EnableBankingClient("app-123", str(rsa_key), "https://api.example.test")
    with pytest.raises(ApiError) as ei:
        c.call("GET", "/accounts/x/transactions", params={"strategy": "longest"})
    assert ei.value.status == 429
    assert seen["url"] == "https://api.example.test/accounts/x/transactions"
    token = seen["headers"]["Authorization"].removeprefix("Bearer ")
    assert jwt.get_unverified_header(token) == {"typ": "JWT", "alg": "RS256", "kid": "app-123"}


def test_client_returns_json_on_success(monkeypatch, rsa_key):
    monkeypatch.setattr(client_mod.requests, "request", lambda *a, **k: FakeResp(200, {"ok": 1}))
    assert EnableBankingClient("a", str(rsa_key)).call("GET", "/application") == {"ok": 1}


def test_only_booked_entries_become_transactions_and_a_dateless_one_never_does(con):
    """A bank sent entries with status OTHR and no date at all: they used to land in `transactions` with a NULL
    booking_date and the analytics could not load the dataset. Anything but BOOK is pending; a booked entry without
    a booking date takes its value date; one with no date at all is not a booked row."""
    other = {k: v for k, v in eb_tx("o1", "2025-10-02", -3.0, "CARTE X", status="OTHR").items()
             if k not in ("booking_date", "value_date")}
    no_bdate = {**eb_tx("b1", "2025-10-03", -4.0, "CARTE Y"), "booking_date": None}
    no_date = {k: v for k, v in eb_tx("b2", "2025-10-04", -5.0, "CARTE Z").items() if k not in ("booking_date", "value_date")}
    fc = FakeClient({"acc1": [{"transactions": [eb_tx("r1", "2025-10-01", -5.0, "CARTE A"), other, no_bdate, no_date]}]})
    res = sync_account(con, fc, "acc1", full=False, force=False, out=lambda *_: None)
    assert res["new"] == 2 and res["pending"] == 2
    assert con.execute("SELECT COUNT(*) FROM transactions WHERE booking_date IS NULL").fetchone()[0] == 0
    assert con.execute("SELECT booking_date FROM transactions WHERE entry_reference='b1'").fetchone()[0] == "2025-10-03"
    assert con.execute("SELECT COUNT(*) FROM pending_transactions").fetchone()[0] == 2
