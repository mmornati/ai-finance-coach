"""Synthetic household for the E3 memory tests (invented people, shops and amounts only)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from coach.db import connect
from helpers import add_bank, add_tx

TODAY = dt.date(2026, 10, 4)

CATEGORIZATION = """\
# Annotations (synthetic test file)
annotations:
  - id: kitchen-works
    match:
      merchant_key: '^BRICO RENOV'
      date_from: 2026-01-01
    category: housing.renovation
    tags: [one_off, capital]            # not a habit
    event: kitchen-2026
    note: Kitchen works paid to the contractor.

  # --- streaming
  - id: streambox-sub
    match:
      merchant_key: '^STREAMBOX'
    category: subscriptions.video_streaming
    note: Family streaming plan.
"""

ASSETS = """\
# Assets not synced
assets:
  - id: savings-book
    kind: regulated_savings          # tax-free savings
    provider: Test Bank
    balance: 5000
    as_of: 2025-12-01
    liquidity: immediate
    connected: false

  - id: family-house
    kind: real_estate
    value: null                      # to fill
    as_of: null
"""

LOAN = """\
# synthetic loan
id: home-loan
kind: mortgage
lender: Homebank
asset: family house
start_date: 2020-01-01
end_date: 2040-01-01
principal: 250000
outstanding: 180000
outstanding_as_of: 2026-01-15       # from the January statement
rate: { type: fixed, nominal: 2.1, taeg: null }
monthly_payment: 1500
insurance: { provider: null, monthly: null, delegated: null }
payment_match: '^HOMEBANK'
documents: []
notes: ""
"""

EVENTS = """\
# Events

## kitchen-2026

- **What:** kitchen renovation.
- **Funding:** savings.
"""

HOUSEHOLD = """\
members:
  - id: anna
    name: Anna Rossi
    role: adult
    birth_year: 1984
    aliases: ["MME ANNA ROSSI", "M OU MME ROSSI ANNA"]
  - id: luca
    name: Luca Rossi
    role: adult
  - id: mia
    name: Mia Rossi
    role: child
    birth_year: 2012
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def make_memory(mem: Path, *, household: bool = True) -> Path:
    mem.mkdir(parents=True, exist_ok=True)
    write(mem / "categorization.yaml", CATEGORIZATION)
    write(mem / "assets.yaml", ASSETS)
    write(mem / "liabilities" / "home-loan.yaml", LOAN)
    write(mem / "events.md", EVENTS)
    write(mem / "preferences.md", "# Preferences\n\n- Tone: short. Talk to Anna about Luca's budget.\n")
    write(mem / "profile.md", "# Profile\n\nAnna Rossi and Luca Rossi live in a house. IBAN FR7630006000011234567890189.\n")
    if household:
        write(mem / "household.yaml", HOUSEHOLD)
    return mem


def monthly(con, uid, key_prefix, desc, amounts, tx_type="card", start=(2025, 10), day=5):
    y, m = start
    for i, amt in enumerate(amounts):
        yy, mm = y + (m - 1 + i) // 12, (m - 1 + i) % 12 + 1
        add_tx(con, uid, f"{key_prefix}{i:02d}", f"{yy}-{mm:02d}-{day:02d}", amt, desc, tx_type)


def label(con, key, category, conf=0.95, source="llm", name=None):
    con.execute("INSERT OR REPLACE INTO merchants VALUES (?,?,?,?,0,?,?,'t')",
                (key, name or key.title(), category, conf, source, "sonnet"))


def make_world(cfg, *, memory: bool = True, household: bool = True):
    """DB with 3 accounts and ~100 transactions; memory files if `memory`. Returns the open connection."""
    con = connect(cfg, insecure=True, create=True)
    add_bank(con, "s1", "Fortuneo", "FR", [("fo", "FR7600000000000000000001", "M OU MME ROSSI ANNA")])
    add_bank(con, "s2", "Caisse d'Epargne X", "FR", [("ce", "FR7600000000000000000002", "CPT COURANT TEST")])
    add_bank(con, "s3", "Revolut", "LT", [("rl", "LT0000000000000000001", "Mia Rossi")])
    con.execute("UPDATE accounts SET owner='joint', purpose='cards' WHERE uid='fo'")
    con.execute("UPDATE accounts SET owner='joint', purpose='main' WHERE uid='ce'")
    con.execute("UPDATE accounts SET owner='mia', purpose='kids' WHERE uid='rl'")
    con.commit()
    monthly(con, "fo", "stream", "STREAMBOX", [-12.99] * 12)
    label(con, "STREAMBOX", "subscriptions.video_streaming")
    monthly(con, "ce", "power", "SUNPOWER ENERGIE", [-85, -82, -88, -85, -84, -86, -85, -87, -83, -85, -85, -86],
            tx_type="direct_debit", day=9)
    label(con, "SUNPOWER ENERGIE", "housing.energy", 0.9)
    monthly(con, "ce", "loan", "HOMEBANK ECH PRET", [-1500] * 12, tx_type="direct_debit", day=3)
    label(con, "HOMEBANK ECH PRET", "housing.mortgage")
    add_tx(con, "fo", "reno1", "2026-03-10", -6500.0, "BRICO RENOV SARL", "card")
    for i in range(70):                                   # bulk so that account shares make sense
        add_tx(con, "fo", f"gro{i:02d}", f"2026-{(i % 9) + 1:02d}-{(i % 27) + 1:02d}", -25.0 - i % 3, "ACME GROCERS", "card")
    label(con, "ACME GROCERS", "food.groceries", 1.0, "user")
    for i in range(5):
        add_tx(con, "fo", f"fm{i}", f"2026-0{i + 1}-14", -40.0, "FRESH MARKET", "card")
    label(con, "FRESH MARKET", "food.groceries", 0.5)
    for i in range(2):
        add_tx(con, "ce", f"pers{i}", f"2026-0{i + 3}-20", -400.0, "LUCA BIANCHI", "transfer_out")
    add_tx(con, "fo", "tr_out", "2026-05-02", -300.0, "VIR TO SAVINGS", "transfer_out")
    add_tx(con, "ce", "tr_in", "2026-05-02", 300.0, "VIR FROM CARDS", "transfer_in")
    con.execute("INSERT INTO transfer_links(out_tx_key, in_tx_key, amount, confidence, method, created_at) "
                "VALUES ('tr_out','tr_in',300,1.0,'manual','t')")
    con.commit()
    if memory:
        make_memory(cfg.memory_dir, household=household)
    return con


def fake_tty(monkeypatch, answers=("y",)):
    """Pretend stdin/stdout are a terminal and feed the typed answers (accept / purge-history are TTY-only)."""
    it = iter(answers)
    monkeypatch.setattr("coach.memory.commands._is_tty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))
