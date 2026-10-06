"""Normalisation: parse every stored descriptor with its bank's parser into tx_enriched (+ tx_parse_meta)."""
from __future__ import annotations

import json

from coach.classify.parsers import parse_tx
from coach.classify.parsers.common import RawTx, household


def normalize_all(con, memory_dir=None) -> int:
    h = household(con, memory_dir)
    accounts = {}
    for uid, bank, iban, ctype, name, country in con.execute(
            """SELECT a.uid, COALESCE(a.bank, s.aspsp_name), a.iban, a.cash_account_type, a.name, s.aspsp_country
               FROM accounts a LEFT JOIN sessions s ON s.session_id=a.session_id"""):
        accounts[uid] = (bank, iban, ctype or "", country)
    rows = con.execute("""SELECT tx_key, description, amount, booking_date, account_uid, currency, counterparty,
                                 bank_tx_code, raw FROM transactions""").fetchall()
    for key, desc, amount, bdate, uid, cur, cp, code, raw in rows:
        bank, iban, ctype, country = accounts.get(uid, (None, None, "", None))
        try:
            raw_d = json.loads(raw) if raw else None
        except ValueError:
            raw_d = None
        tx = RawTx(desc or "", amount, bdate or "", cur or "EUR", cp or "", code or "", ctype, bank or "", raw_d)
        p = parse_tx(tx, h, bank=bank, iban=iban, country=country)
        con.execute("INSERT OR REPLACE INTO tx_enriched VALUES (?,?,?,?,?,?,?)",
                    (key, p["tx_type"], p["op_date"], p["merchant_raw"], p["merchant_key"],
                     p["fx_amount"], p["fx_currency"]))
        con.execute("INSERT OR REPLACE INTO tx_parse_meta VALUES (?,?,?,?,?,?)",
                    (key, p["parser"], p["counterparty"], p["mandate_ref"], p["creditor_id"], p["reference"]))
    con.commit()
    return len(rows)
