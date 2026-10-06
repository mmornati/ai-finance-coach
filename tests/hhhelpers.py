"""Synthetic household for the E14 tests (people, owners, attribution, children's money, who pays what, logins).

Every name, bank, merchant and amount here is invented. The world extends ``memhelpers.make_world``:

    fo  joint cards account (bank "Fortuneo")      ce  joint main account      rl  Mia's own account ("Revolut", purpose kids)
    an  Anna's personal account ("Anna Bank")      lu  Luca's personal account ("Luca Bank")
    nk  Noa's prepaid card account ("Prepaid X", purpose kids, owner joint: a rule attributes it to Noa)

Members (memhelpers.HOUSEHOLD plus Noa): anna, luca (adults), mia, noa (children).
"""
from __future__ import annotations

import datetime as dt

from anhelpers import D
from coach.analytics.common import add_months
from helpers import add_bank, add_tx
from memhelpers import label, make_world

TODAY = dt.date(2026, 10, 4)

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
  - id: noa
    name: Noa Rossi
    role: child
    birth_year: 2015
"""


def hh_world(cfg, *, household: str = HOUSEHOLD, extra_yaml: str = ""):
    """The memory world with the five accounts, six months of pocket money for Mia, one extra top-up, payments by Mia and Noa."""
    con = make_world(cfg)
    (cfg.memory_dir / "household.yaml").write_text(household + extra_yaml)
    add_bank(con, "s4", "Anna Bank", "FR", [("an", "FR7600000000000000000004", "MME ANNA ROSSI")])
    add_bank(con, "s5", "Luca Bank", "FR", [("lu", "FR7600000000000000000005", "M LUCA ROSSI")])
    add_bank(con, "s6", "Prepaid X", "FR", [("nk", "FR7600000000000000000006", "PREPAID NOA")])
    con.execute("UPDATE accounts SET owner='anna', purpose='main' WHERE uid='an'")
    con.execute("UPDATE accounts SET owner='luca', purpose='main' WHERE uid='lu'")
    con.execute("UPDATE accounts SET owner='joint', purpose='kids' WHERE uid='nk'")
    con.commit()
    for i in range(6):                                                    # pocket money: 10 EUR on the 5th, Anna -> Mia, a day apart across banks
        m = add_months(D("2026-04-05"), i)
        add_tx(con, "an", f"pmo{i}", m.isoformat(), -10.0, "VIR MIA ROSSI ARGENT DE POCHE", "person_transfer_out")
        add_tx(con, "rl", f"pmi{i}", (m + dt.timedelta(days=1)).isoformat(), 10.0, "VIREMENT RECU DE ANNA ROSSI", "person_transfer_in")
    add_tx(con, "lu", "xo1", "2026-09-12", -25.0, "VIR MIA ROSSI ANNIVERSAIRE", "person_transfer_out")      # an extra top-up from Luca
    add_tx(con, "rl", "xi1", "2026-09-14", 25.0, "VIREMENT RECU DE LUCA ROSSI", "person_transfer_in")
    add_tx(con, "rl", "gift1", "2026-08-20", 30.0, "VIR GRAND MERE CADEAU", "person_transfer_in")           # an extra top-up from outside
    for i, (d, amt, desc) in enumerate([("2026-09-02", -4.5, "CARTE BOULANGERIE DU COIN"), ("2026-09-09", -12.0, "CARTE JEUXVIDEO SHOP"),
                                        ("2026-09-16", -3.2, "CARTE BOULANGERIE DU COIN"), ("2026-09-23", -9.9, "CARTE JEUXVIDEO SHOP"),
                                        ("2026-10-01", -5.0, "CARTE BOULANGERIE DU COIN")]):
        add_tx(con, "rl", f"mp{i}", d, amt, desc, "card")
    label(con, "CARTE BOULANGERIE DU COIN", "food.restaurants", 0.95, name="Boulangerie du coin")
    label(con, "CARTE JEUXVIDEO SHOP", "leisure.hobbies", 0.95, name="Jeux video shop")
    for i, (d, amt, desc) in enumerate([("2026-09-05", -6.0, "CARTE SNACK BAR NOA"), ("2026-09-19", -7.5, "CARTE SNACK BAR NOA")]):
        add_tx(con, "nk", f"np{i}", d, amt, desc, "card")
    label(con, "CARTE SNACK BAR NOA", "food.restaurants", 0.95, name="Snack bar")
    # a card of Mia on the shared card account: the bank prints the last four digits
    add_tx(con, "fo", "mc1", "2026-09-27", -14.0, "CARTE X4242 CINEMA LE ROYAL", "card")
    add_tx(con, "fo", "mc2", "2026-09-28", -22.0, "CARTE X1111 SUPERMARCHE", "card")
    label(con, "CARTE X4242 CINEMA LE ROYAL", "leisure.cinema_events", 0.95, name="Cinema")
    label(con, "CARTE X1111 SUPERMARCHE", "food.groceries", 0.95, name="Supermarche")
    con.execute("INSERT INTO balances VALUES ('rl','2026-10-04T08:00:00+00:00','CLBD',57.0,'EUR','2026-10-04')")
    con.execute("INSERT INTO balances VALUES ('an','2026-10-04T08:00:00+00:00','CLBD',900.0,'EUR','2026-10-04')")
    con.commit()
    return con


def match_all(con, cfg):
    """Pair the cross-bank transfers the way the scheduled job does (memory members as evidence)."""
    from coach import transfers as T
    return T.match_transfers(con, cfg.transfer_window_days, auto_link=True, **T.opts(cfg))
