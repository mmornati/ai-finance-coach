"""Synthetic household for the E8 tests (subscriptions & contracts). Every name, merchant and amount is invented.

Recurring series on top of the E3 world (make_world: STREAMBOX 12.99 monthly, SUNPOWER energy ~85, HOMEBANK loan):
    TELCOCO      telecom        29.99 monthly, 18 payments from 2025-04 (more than 12 months)     -> contract telco (commitment to 2027-01-15)
    HOMESURE     home insurance 22.50 monthly, 24 payments from 2024-11                          -> NO contract file
    CLOUDBOX     software       4.99 x 6 then 5.99 x 3 monthly (a price rise)                    -> NO contract file
    FITCLUB      membership     39.90 monthly, 12 payments                                       -> NO contract file
    OLDAPP       software       3.99 monthly, ended in 2026-02 (more than 1.5 cadences ago)
Contract files: streambox (usage never, last used 2026-06-01) and telco.
"""
from __future__ import annotations

import datetime as dt

from helpers import add_tx
from memhelpers import label, make_world, monthly

TODAY = dt.date(2026, 10, 4)

STREAMBOX = """\
id: streambox
provider: StreamBox
kind: streaming
merchant_match: '^STREAMBOX'
start_date: 2025-10-05
billing: { amount: 12.99, period: monthly }
usage: { frequency: never, last_used: 2026-06-01, note: "nobody watches it" }
documents: []
notes: ""
"""

TELCO = """\
id: telco
provider: TelcoCo
kind: telecom
holder: anna
merchant_match: '^TELCOCO'
start_date: 2025-04-10
commitment_end: 2027-01-15
notice_period_days: 10
billing: { amount: 29.99, period: monthly }
contract_number: TC-778899
documents: []
notes: ""
"""


def build_subs_world(cfg, *, contracts: bool = True):
    con = make_world(cfg)
    monthly(con, "fo", "tel", "TELCOCO MOBILE", [-29.99] * 18, tx_type="direct_debit", start=(2025, 4), day=10)
    label(con, "TELCOCO MOBILE", "subscriptions.telecom")
    monthly(con, "fo", "hs", "HOMESURE ASSURANCES", [-22.50] * 24, tx_type="direct_debit", start=(2024, 11), day=2)
    label(con, "HOMESURE ASSURANCES", "housing.home_insurance")
    monthly(con, "fo", "cb", "CLOUDBOX", [-4.99] * 6 + [-5.99] * 3, start=(2026, 1), day=6)
    label(con, "CLOUDBOX", "subscriptions.software_cloud")
    monthly(con, "fo", "fit", "FITCLUB", [-39.90] * 12, tx_type="direct_debit", start=(2025, 10), day=12)
    label(con, "FITCLUB", "leisure.sports_activities")
    monthly(con, "fo", "old", "OLDAPP", [-3.99] * 8, start=(2025, 6), day=8)
    label(con, "OLDAPP", "subscriptions.software_cloud")
    con.commit()
    if contracts:
        (cfg.memory_dir / "contracts").mkdir(parents=True, exist_ok=True)
        (cfg.memory_dir / "contracts" / "streambox.yaml").write_text(STREAMBOX)
        (cfg.memory_dir / "contracts" / "telco.yaml").write_text(TELCO)
    return con
