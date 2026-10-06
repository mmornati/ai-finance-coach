"""Synthetic test data only (no real bank data)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Verbatim copy of the two prototype SCHEMA strings (eb.py + classify.py), independent of coach.migrations.
PROTOTYPE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, aspsp_name TEXT, aspsp_country TEXT,
  valid_until TEXT, created_at TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS accounts (
  uid TEXT PRIMARY KEY, session_id TEXT, name TEXT, iban TEXT, currency TEXT,
  cash_account_type TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS transactions (
  tx_key TEXT PRIMARY KEY,
  account_uid TEXT, entry_reference TEXT, booking_date TEXT, value_date TEXT,
  amount REAL, currency TEXT, counterparty TEXT, description TEXT,
  mcc TEXT, bank_tx_code TEXT, raw TEXT, first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS pending_transactions (
  account_uid TEXT, booking_date TEXT, amount REAL, currency TEXT,
  counterparty TEXT, description TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS balances (
  account_uid TEXT, fetched_at TEXT, balance_type TEXT, amount REAL, currency TEXT,
  reference_date TEXT);
CREATE TABLE IF NOT EXISTS sync_log (
  account_uid TEXT, ran_at TEXT, ok INTEGER, new_tx INTEGER, pages INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS pending_auth (
  state TEXT PRIMARY KEY, aspsp_name TEXT, aspsp_country TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS tx_enriched (
  tx_key TEXT PRIMARY KEY, tx_type TEXT, op_date TEXT, merchant_raw TEXT,
  merchant_key TEXT, fx_amount REAL, fx_currency TEXT);
CREATE TABLE IF NOT EXISTS merchants (
  merchant_key TEXT PRIMARY KEY, merchant_name TEXT, category TEXT, confidence REAL,
  recurring_hint INTEGER, source TEXT, model TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS tx_overrides (tx_key TEXT PRIMARY KEY, category TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS merchant_eval (
  merchant_key TEXT, model TEXT, category TEXT, confidence REAL, run_at TEXT,
  PRIMARY KEY (merchant_key, model));
"""

FAKE_TXS = [
    # (booking_date, amount, description)
    ("2025-10-03", -4.50, "CARTE 02/10 RELAY BEAUVAIS"),
    ("2025-10-04", -12.30, "CARTE 03/10 MOL*BOULANGERIE PAUL 33700 BEAUVAIS"),
    ("2025-10-05", -25.00, "VIR INST WERO Jean MARTIN"),
    ("2025-10-06", -60.00, "RET DAB 12345 BANQUE TEST BORDEAUX"),
    ("2025-10-07", -87.10, "CARTE 05/10 AIRBNB PARIS 95,00 USD COURS 1,09"),
    ("2025-10-08", 1500.00, "VIR SEPA EMPLOYEUR SA SALAIRE"),
    ("2025-10-09", -9.99, "CARTE 08/10 NETFLIX.COM"),
]


def make_prototype_db(path: Path, with_enriched: bool = False) -> Path:
    """A prototype-schema plaintext DB with synthetic rows (incl. pending_auth states)."""
    con = sqlite3.connect(path)
    con.executescript(PROTOTYPE_SCHEMA)
    con.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw) VALUES ('s1','Fortuneo','FR','2099-01-01','2026-10-01','{}')")
    con.execute("INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw) VALUES ('acc1','s1','MR ALICE TESTOWNER','FR7600000000000000000001234',"
                "'EUR','CACC','{}')")
    for i, (d, amt, desc) in enumerate(FAKE_TXS):
        con.execute("INSERT INTO transactions(tx_key, account_uid, entry_reference, booking_date, value_date, amount, currency, counterparty, description, mcc, bank_tx_code, raw, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"acc1:ref:{i}", "acc1", str(i), d, d, amt, "EUR", "", desc, None, "", "{}", d, d))
    con.execute("INSERT INTO balances VALUES ('acc1','2026-10-01','CLBD',1234.5,'EUR','2026-10-01')")
    con.execute("INSERT INTO sync_log VALUES ('acc1','2026-10-01T00:00:00+00:00',1,7,1,'x')")
    for st in ("state-aaa", "state-bbb"):
        con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at) VALUES (?,?,?,?)", (st, "Test Bank", "FR", datetime.now(timezone.utc).isoformat(timespec="seconds")))
    con.execute("INSERT INTO merchants VALUES ('RELAY BEAUVAIS','Relay','shopping.books_media',0.9,0,'llm','sonnet','t')")
    con.execute("INSERT INTO merchant_eval VALUES ('RELAY BEAUVAIS','haiku','shopping.books_media',0.8,'t')")
    con.commit()
    con.close()
    return path


class FakeClient:
    """Scripted Enable Banking client (no network). `pages` maps account uid -> list of page dicts
    (or an Exception to raise at that page). Also answers the session endpoints:
      codes    {code: POST /sessions payload}     live  {session_id: GET /sessions/{id} payload or Exception}
      fail_accounts {uid: Exception}  raised for any /accounts/{uid}/... call (a whole bank down)"""

    def __init__(self, pages=None, balances=None, codes=None, live=None, fail_accounts=None):
        self.pages = pages or {}
        self.balances = balances or {"balances": []}
        self.codes = codes or {}
        self.live = live or {}
        self.fail_accounts = fail_accounts or {}
        self.calls: list[tuple[str, str, dict]] = []
        self._idx: dict[str, int] = {}

    def call(self, method, path, **kw):
        params = dict(kw.get("params") or {})
        self.calls.append((method, path, params if "json" not in kw else kw["json"]))
        if path == "/auth":
            return {"url": "https://bank.test/auth?state=" + kw["json"]["state"]}
        if path == "/sessions" and method == "POST":
            return self.codes[kw["json"]["code"]]
        if path.startswith("/sessions/"):
            r = self.live.get(path.split("/")[2], {"status": "AUTHORIZED"})
            if isinstance(r, Exception):
                raise r
            return r
        if path.startswith("/accounts/") and path.split("/")[2] in self.fail_accounts:
            raise self.fail_accounts[path.split("/")[2]]
        if path.endswith("/transactions"):
            uid = path.split("/")[2]
            i = self._idx.get(uid, 0)
            self._idx[uid] = i + 1
            page = self.pages[uid][i]
            if isinstance(page, Exception):
                raise page
            return page
        if path.endswith("/balances"):
            return self.balances
        raise AssertionError(f"unexpected call {method} {path}")


def session_payload(sid, bank, country, accounts, valid_until="2099-01-01T00:00:00Z"):
    """POST /sessions response. accounts: [(uid, iban, name)]"""
    return {"session_id": sid, "aspsp": {"name": bank, "country": country},
            "access": {"valid_until": valid_until},
            "accounts": [{"uid": u, "name": n, "currency": "EUR", "cash_account_type": "CACC",
                          "account_id": {"iban": i}} for u, i, n in accounts]}


def add_bank(con, sid, bank, country, accounts, valid_until="2099-01-01T00:00:00+00:00"):
    """Insert a session + accounts directly. accounts: [(uid, iban, name)]"""
    con.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw) "
                "VALUES (?,?,?,?,?,?)", (sid, bank, country, valid_until, "2026-01-01T00:00:00+00:00", "{}"))
    for u, iban, name in accounts:
        con.execute("INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw, bank) "
                    "VALUES (?,?,?,?,?,?,?,?)", (u, sid, name, iban, "EUR", "CACC", "{}", bank))
    con.commit()


def add_tx(con, uid, key, date, amount, desc, tx_type=None):
    con.execute("INSERT INTO transactions(tx_key, account_uid, entry_reference, booking_date, value_date, amount, "
                "currency, counterparty, description, mcc, bank_tx_code, raw, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,'{}',?,?)",
                (key, uid, key, date, date, amount, "EUR", "", desc, date, date))
    if tx_type is not None:
        con.execute("INSERT OR REPLACE INTO tx_enriched VALUES (?,?,NULL,?,?,NULL,NULL)", (key, tx_type, desc, desc))
    con.commit()


def eb_tx(ref, date, amount, desc, status="BOOK", debit=True, creditor="SHOP"):
    return {
        "entry_reference": ref, "booking_date": date, "value_date": date, "status": status,
        "transaction_amount": {"amount": f"{abs(amount):.2f}", "currency": "EUR"},
        "credit_debit_indicator": "DBIT" if debit else "CRDT",
        "creditor": {"name": creditor}, "remittance_information": [desc],
    }
