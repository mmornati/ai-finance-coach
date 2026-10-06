"""Synthetic rental property for the E15 tests. Every name, bank, merchant, figure and place below is invented.

    rn   the property account (bank "Rentbank", owner joint, purpose rental) with nine months (Jan-Sep 2026) of flows;
    asset ``rental-flat-1`` (kind real_estate_rental, account rn, loan rental-loan, scheme "pinel", a 9-year commitment from 2021-03-05);
    loan ``rental-loan`` (160,000 at 3.4 % from 2021-03-01 for 240 months), debited from rn.

The monthly flows (TODAY is 2026-10-04, so Jan-Sep are the closed months), all hand-computed in the tests:

    rent 620 on the 3rd, except: February (a declared vacancy), May (nothing: reported), June (paid on 1 July: the next month holds two rents);
    loan 900 on the 1st; co-ownership 35; a 31 EUR management fee per rent received; PNO insurance 25 in Jan / Apr / Jul; property tax 800
    in March; works 450 in April; bank fee 2.25 a month; the owner's own transfer of 400 each month (never rent, never a cost).
"""
from __future__ import annotations

import datetime as dt

from helpers import add_bank, add_tx
from memhelpers import ASSETS, label, make_world, write

RENTAL_ASSET = """
  - id: rental-flat-1
    kind: real_estate_rental
    provider: Alphagest Gestion
    property_manager: Alphagest Gestion
    description: Flat at 12 rue des Lilas, Marville
    notes: Tenant Mme Duvalier, key with the concierge of 12 rue des Lilas
    account: Rentbank courant
    loan: rental-loan
    scheme: pinel
    value: 200000
    as_of: 2026-09-01
    rent_monthly: 620
    purchase_price: 190000
    purchase_date: 2021-02-01
    commitment:
      start_date: 2021-03-05
      years: 9
      surface_m2: 40
      rent_cap_monthly: 600
      tenant_income_limit: 30000
      tenant_income: 28000
      reduction_rate_pct: 18
      reduction_first_year: 2021
    vacancies:
      - start: 2026-02-01
        end: 2026-02-28
        note: works between two tenants
"""
LOAN = """\
id: rental-loan
kind: mortgage
lender: Prêtbank Immobilier
asset: rental-flat-1
start_date: 2021-03-01
end_date: 2041-03-01
term_months: 240
principal: 160000
rate: { type: fixed, nominal: 3.4 }
monthly_payment: 919.74
debited_account: rn
payment_match: 'LENDERCO'
documents: []
notes: ""
"""
SENTINELS = ["alphagest", "lilas", "marville", "duvalier", "concierge", "prêtbank", "pretbank", "rentbank", "tilleuls"]


def _m(y, m, d) -> str:
    return dt.date(y, m, d).isoformat()


def rental_world(cfg, *, loan: bool = True, asset: str = RENTAL_ASSET):
    con = make_world(cfg)
    write(cfg.memory_dir / "assets.yaml", ASSETS.rstrip("\n") + "\n" + asset)
    if loan:
        write(cfg.memory_dir / "liabilities" / "rental-loan.yaml", LOAN)
    add_bank(con, "s9", "Rentbank", "FR", [("rn", "FR7600000000000000000099", "Rentbank courant")])
    con.execute("UPDATE accounts SET owner='joint', purpose='rental' WHERE uid='rn'")
    con.commit()
    rents = [(1, 3), (3, 3), (4, 3), (7, 1), (7, 3), (8, 3), (9, 3)]
    for i, (mo, day) in enumerate(rents):
        add_tx(con, "rn", f"rent{i}", _m(2026, mo, day), 620.0, "VIR LOYER LOCATAIRE TEST", "transfer_in")
        add_tx(con, "rn", f"fee{i}", _m(2026, mo, day), -31.0, "FRAIS DE GESTION LOCATIVE ALPHAGEST", "direct_debit")
    for mo in range(1, 10):
        add_tx(con, "rn", f"loan{mo}", _m(2026, mo, 1), -900.0, "ECH PRET LENDERCO", "loan_payment")
        add_tx(con, "rn", f"copro{mo}", _m(2026, mo, 5), -35.0, "PRLV SYNDIC DES TILLEULS", "direct_debit")
        add_tx(con, "rn", f"bank{mo}", _m(2026, mo, 30 if mo == 9 else 28), -2.25, "F TENUE DE COMPTE", "bank_fee")
        add_tx(con, "rn", f"topup{mo}", _m(2026, mo, 2), 400.0, "VIR DEPUIS COMPTE COURANT", "transfer_in")
    for mo in (1, 4, 7):
        add_tx(con, "rn", f"pno{mo}", _m(2026, mo, 10), -25.0, "PRLV ASSURANCE PNO MUTUELLE", "direct_debit")
    add_tx(con, "rn", "tf1", _m(2026, 3, 20), -800.0, "PRLV DGFIP TAXE FONCIERE 2026", "direct_debit")
    add_tx(con, "rn", "works1", _m(2026, 4, 15), -450.0, "BRICO RENOV SARL", "card")
    label(con, "VIR DEPUIS COMPTE COURANT", "transfer.internal", 1.0, "user")
    label(con, "BRICO RENOV SARL", "housing.renovation", 0.95)
    con.execute("INSERT INTO balances VALUES ('rn','2026-10-04T08:00:00+00:00','CLBD',150.0,'EUR','2026-10-04')")
    con.commit()
    return con
