"""Build a fully SYNTHETIC demo home for the documentation, the screenshots and the "try it" guide.

    uv run python scripts/demo/seed_demo.py --home /tmp/coach-demo        # then: scripts/demo/run_demo.sh /tmp/coach-demo

Every person, bank, merchant, address and amount below is INVENTED (the same vocabulary as the test suite: the Rossi household,
Anna and Luca with Mia and Noa, ACME GROCERS, STREAMBOX, TELCOCO ...). Nothing is read from a real home: the script refuses a
folder that is not empty unless it carries the demo marker it writes itself, and every path, the database and the secrets
(file backend, never the Keychain) live inside that folder. The data runs over the 24 months before today, so the demo always
looks "current".

The database is a PLAINTEXT SQLite file (`insecure_plaintext_db = true`): fine for invented data, never for a real home.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import shutil
import sys
from pathlib import Path

MARKER = ".coach-demo-home"


# ------------------------------------------------------------------------------------------------------------------ home

CONFIG = """\
# DEMO home (synthetic data): written by scripts/demo/seed_demo.py. Never point it at real data.
data_dir = "data"
memory_dir = "memory"
insecure_plaintext_db = true          # demo only: the data is invented

[llm]
backend = "claude-code"
model = "sonnet"

[coach]
backend = "claude-code"
claude_env = ["FAKE_CLAUDE"]          # the demo's scripted `claude` (scripts/demo/bin) reads its answer from this variable

[ui]
port = {port}
open_browser = false

[memory]
history = true

[alerts]
enabled = true
"""


def prepare_home(home: Path, port: int, force: bool) -> None:
    if home.exists() and any(home.iterdir()):
        if not (home / MARKER).exists():
            sys.exit(f"refusing: {home} is not empty and is not a demo home (no {MARKER}). Pick an empty folder.")
        if not force:
            sys.exit(f"{home} already holds a demo: pass --force to rebuild it")
        shutil.rmtree(home)
    for sub in ("data", "memory", "config", "secrets"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    os.chmod(home / "secrets", 0o700)
    (home / MARKER).write_text("synthetic demo home\n")
    (home / "config.toml").write_text(CONFIG.format(port=port))
    env = {"COACH_HOME": str(home), "COACH_CONFIG": str(home / "config.toml"), "COACH_CONFIG_DIR": str(home / "config"),
           "COACH_SECRETS_BACKEND": "file", "COACH_SECRETS_DIR": str(home / "secrets")}
    for k in [k for k in os.environ if k.startswith(("COACH_", "EB_"))] + ["ANTHROPIC_API_KEY"]:
        os.environ.pop(k, None)
    os.environ.update(env)
    (home / "demo.env").write_text("".join(f"export {k}={v}\n" for k, v in env.items()))


# ------------------------------------------------------------------------------------------------------------------ dates

def add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m]
    return dt.date(y, m + 1, min(d.day, last))


def day_in(month: dt.date, day: int) -> dt.date:
    nxt = add_months(month.replace(day=1), 1)
    return month.replace(day=min(day, (nxt - dt.timedelta(days=1)).day))


def remaining(principal: float, rate_pct: float, months: int, paid: int) -> float:
    """Capital still due after `paid` instalments of a fixed-rate annuity (what a yearly statement prints)."""
    r = rate_pct / 100 / 12
    a = principal * r / (1 - (1 + r) ** -months)
    return round(principal * (1 + r) ** paid - a * ((1 + r) ** paid - 1) / r, 2)


def annuity(principal: float, rate_pct: float, months: int) -> float:
    r = rate_pct / 100 / 12
    return round(principal * r / (1 - (1 + r) ** -months), 2)


# ------------------------------------------------------------------------------------------------------------------ world

ACCOUNTS = [
    # uid, session, bank, display name, owner, purpose, label, source
    ("aur-joint", "s-aurore", "Banque Aurore", "M OU MME ROSSI", "joint", "main", "Joint account", "api"),
    ("aur-livret", "s-aurore", "Banque Aurore", "LIVRET A ROSSI", "joint", "savings", "Savings book", "api"),
    ("nova-anna", "s-nova", "Nova Bank", "ANNA ROSSI", "anna", "main", "Anna - Nova", "api"),
    ("nova-luca", "s-nova", "Nova Bank", "LUCA ROSSI", "luca", "main", "Luca - Nova", "api"),
    ("nova-mia", "s-nova-kids", "Nova Bank", "MIA ROSSI JUNIOR", "mia", "kids", "Mia - junior", "api"),
    ("hor-rental", "s-horizon", "Credit Horizon", "SCI ROSSI LOCATION", "joint", "rental", "Rental flat account", "api"),
    ("pre-noa", None, "Prepaid card", "CARTE PREPAYEE NOA", "joint", "kids", "Noa - prepaid card", "import"),
]
SESSIONS = [  # session, bank, country, days of consent left
    ("s-aurore", "Banque Aurore", "FR", 142), ("s-nova", "Nova Bank", "FR", 9), ("s-nova-kids", "Nova Bank", "FR", 61),
    ("s-horizon", "Credit Horizon", "FR", 87),
]

# merchant key -> (display name, category, confidence, source)
M = {
    "SALAIRE NORDWIND SYSTEMS": ("Nordwind Systems", "income.salary", 1.0, "user"),
    "SALAIRE BELLAVISTA HOTELS": ("Bellavista Hotels", "income.salary", 1.0, "user"),
    "HOMEBANK ECH PRET": ("Homebank", "housing.mortgage", 1.0, "user"),
    "SUNPOWER ENERGIE": ("SunPower", "housing.energy", 0.97, "llm"),
    "AQUALIS EAUX": ("Aqualis", "housing.water", 0.95, "llm"),
    "HOMESURE ASSURANCES": ("HomeSure", "housing.home_insurance", 0.96, "llm"),
    "TELCOCO BOX": ("TelcoCo", "subscriptions.telecom", 0.98, "llm"),
    "PIXELTEL MOBILE": ("PixelTel", "subscriptions.telecom", 0.95, "llm"),
    "STREAMBOX": ("StreamBox", "subscriptions.video_streaming", 0.99, "llm"),
    "VIDEOMAX": ("VideoMax", "subscriptions.video_streaming", 0.93, "llm"),
    "SOUNDWAVE FAMILY": ("SoundWave", "subscriptions.music_streaming", 0.97, "llm"),
    "CLOUDBOX": ("CloudBox", "subscriptions.software_cloud", 0.95, "llm"),
    "OLDAPP": ("OldApp", "subscriptions.software_cloud", 0.9, "llm"),
    "DAILY HERALD": ("Daily Herald", "subscriptions.news_media", 0.92, "llm"),
    "FITCLUB": ("FitClub", "leisure.sports_activities", 0.96, "llm"),
    "ACME GROCERS": ("Acme Grocers", "food.groceries", 1.0, "user"),
    "FRESH MARKET": ("Fresh Market", "food.groceries", 0.94, "llm"),
    "BOULANGERIE DU COIN": ("Boulangerie du coin", "food.groceries", 0.9, "knn"),
    "PRIMEUR DU MARCHE": ("Primeur du marche", "food.groceries", 0.88, "llm"),
    "TRATTORIA BELLA": ("Trattoria Bella", "food.restaurants", 0.95, "llm"),
    "LE PETIT BISTROT": ("Le Petit Bistrot", "food.restaurants", 0.93, "llm"),
    "BURGER STATION": ("Burger Station", "food.fast_food", 0.96, "llm"),
    "FOODRUN": ("FoodRun", "food.food_delivery", 0.97, "llm"),
    "CAFE DES ARTS": ("Cafe des Arts", "food.cafes_bars", 0.9, "llm"),
    "CANTINE ENTREPRISE": ("Company canteen", "food.work_meals", 0.92, "user"),
    "PETROLINE": ("Petroline", "transport.fuel", 0.98, "llm"),
    "AUTOROUTE DU SUD": ("Autoroute du Sud", "transport.parking_tolls", 0.97, "llm"),
    "PARKING CENTRE": ("Parking Centre", "transport.parking_tolls", 0.9, "llm"),
    "METROPASS": ("MetroPass", "transport.public_transport", 0.95, "llm"),
    "GARAGE DU PONT": ("Garage du Pont", "transport.car_maintenance", 0.9, "llm"),
    "AUTOSURE": ("AutoSure", "transport.car_insurance", 0.96, "llm"),
    "MOBILEASE LOA": ("MobiLease", "transport.car_loan_lease", 1.0, "user"),
    "MEGAMART ONLINE": ("MegaMart", "shopping.marketplace", 0.97, "llm"),
    "MODA URBANA": ("Moda Urbana", "shopping.clothing", 0.94, "llm"),
    "TECHNOPOLIS": ("Technopolis", "shopping.electronics", 0.95, "llm"),
    "ATHLETICA": ("Athletica", "shopping.sports_gear", 0.93, "llm"),
    "LIBRAIRIE DES QUAIS": ("Librairie des Quais", "shopping.books_media", 0.92, "llm"),
    "MAISON DECO": ("Maison Deco", "housing.furniture", 0.9, "llm"),
    "BRICOMAX": ("BricoMax", "housing.maintenance_diy", 0.94, "llm"),
    "BRICO RENOV SARL": ("Brico Renov", "housing.renovation", 1.0, "user"),
    "PHARMACIE CENTRALE": ("Pharmacie Centrale", "health.pharmacy", 0.97, "llm"),
    "CABINET MEDICAL DES PINS": ("Cabinet medical", "health.doctors", 0.92, "llm"),
    "MUTUELLE HARMONIA": ("Harmonia", "health.health_insurance", 0.98, "llm"),
    "REMB MUTUELLE HARMONIA": ("Harmonia refund", "income.refund", 0.95, "llm"),
    "OPTIQUE VISION": ("Optique Vision", "health.optician", 0.9, "llm"),
    "SALON LUMIERE": ("Salon Lumiere", "personal_care.hair_beauty", 0.9, "llm"),
    "CINEMA LE ROYAL": ("Cinema Le Royal", "leisure.cinema_events", 0.95, "llm"),
    "PARC AVENTURA": ("Parc Aventura", "leisure.cinema_events", 0.88, "llm"),
    "JEUXVIDEO SHOP": ("Jeux video shop", "leisure.hobbies", 0.9, "llm"),
    "ECOLE DE MUSIQUE": ("Music school", "kids.activities_toys", 0.93, "user"),
    "CANTINE SCOLAIRE": ("School canteen", "kids.school", 0.97, "user"),
    "CENTRE DE LOISIRS": ("Holiday camp", "kids.activities_toys", 0.9, "llm"),
    "TOYLAND": ("Toyland", "kids.activities_toys", 0.93, "llm"),
    "AIRVOLA": ("AirVola", "travel.flights", 0.98, "llm"),
    "HOTEL MIRAMARE": ("Hotel Miramare", "travel.lodging", 0.95, "llm"),
    "CHALET DES CIMES": ("Chalet des Cimes", "travel.lodging", 0.94, "llm"),
    "RAILEXPRESS": ("RailExpress", "travel.trains", 0.97, "llm"),
    "RENTACAR": ("RentaCar", "travel.car_rental", 0.95, "llm"),
    "CLEANHOME SERVICES": ("CleanHome", "housing.maintenance_diy", 0.86, "llm"),
    "SOLIDARITE MONDE": ("Solidarite Monde", "charity.donations", 0.96, "user"),
    "DGFIP TAXE FONCIERE": ("Property tax", "housing.property_tax", 1.0, "rule"),
    "DGFIP TAXE HABITATION": ("Local tax", "taxes.taxes", 0.95, "rule"),
    "COTISATION CARTE": ("Card fee", "fees.bank_fees", 1.0, "rule"),
    "INTERETS LIVRET": ("Savings interest", "income.interest", 1.0, "rule"),
    "LENDERCO ECH PRET": ("Lenderco", "housing.rental_property_loan", 1.0, "user"),
    "LOYER LOCATAIRE APPT 2B": ("Tenant rent", "income.rental", 1.0, "user"),
    "GESTION LOCATIVE AGENCE ALPHA": ("Letting agency", "housing.property_management", 1.0, "rule"),
    "SYNDIC RESIDENCE LES TILLEULS": ("Co-ownership charges", "housing.property_charges", 0.97, "llm"),
    "ASSURANCE PNO PROPRIO": ("Landlord insurance", "housing.property_insurance", 0.95, "llm"),
    "SNACK BAR DU COLLEGE": ("Snack bar", "food.fast_food", 0.9, "llm"),
    "BD ET MANGAS": ("BD et mangas", "shopping.books_media", 0.9, "llm"),
    # the review queue: labels the app is unsure about, and one it could not label
    "SQ LA FABRIQUE": ("La Fabrique", "leisure.hobbies", 0.55, "llm"),
    "PAYPAL GREENLEAF": ("Greenleaf", "shopping.marketplace", 0.6, "llm"),
    "MARCHE NOCTURNE": ("Marche nocturne", "food.restaurants", 0.5, "llm"),
    "ZEN SPA": ("Zen Spa", "personal_care.hair_beauty", 0.64, "llm"),
}
UNLABELLED = ("SUMUP CHEZ NINA", "TPE 0457 KIOSQUE PLAGE")


class World:
    def __init__(self, con, today: dt.date, seed: int = 7):
        self.con, self.today, self.rnd, self.n = con, today, random.Random(seed), 0
        self.links: list[tuple[str, str, float]] = []

    def tx(self, acct: str, date: dt.date, amount: float, key: str, tx_type: str, desc: str | None = None) -> str | None:
        if date >= self.today:
            return None
        self.n += 1
        k = f"demo{self.n:06d}"
        d = date.isoformat()
        desc = desc or key
        self.con.execute("INSERT INTO transactions(tx_key, account_uid, entry_reference, booking_date, value_date, amount, currency, "
                         "counterparty, description, mcc, bank_tx_code, raw, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,'{}',?,?)",
                         (k, acct, k, d, d, round(amount, 2), "EUR", "", desc, d, d))
        self.con.execute("INSERT OR REPLACE INTO tx_enriched VALUES (?,?,NULL,?,?,NULL,NULL)", (k, tx_type, desc, key))
        return k

    def transfer(self, out_acct: str, in_acct: str, date: dt.date, amount: float, out_desc: str, in_desc: str,
                 tx_type: str = "internal_transfer", in_date: dt.date | None = None) -> None:
        o = self.tx(out_acct, date, -amount, out_desc, tx_type)
        i = self.tx(in_acct, in_date or date, amount, in_desc, tx_type if tx_type == "internal_transfer" else tx_type.replace("_out", "_in"))
        if o and i:
            self.links.append((o, i, amount))

    def r(self, lo: float, hi: float) -> float:
        return round(self.rnd.uniform(lo, hi), 2)


def build_transactions(w: World) -> dict:
    today = w.today
    first = add_months(today.replace(day=1), -24)
    months = [add_months(first, i) for i in range(25)]
    mortgage = annuity(280000, 1.35, 300)
    rental_loan = annuity(165000, 1.6, 240)
    jan = dt.date(today.year, 1, 15)
    paid = lambda start: (jan.year - start.year) * 12 + jan.month - start.month             # noqa: E731
    facts = {"mortgage_payment": round(mortgage + 31.5, 2), "mortgage_annuity": mortgage, "rental_loan_payment": rental_loan,
             "statement_date": jan.isoformat(),
             "mortgage_outstanding": remaining(280000, 1.35, 300, paid(dt.date(2019, 7, 1))),
             "rental_outstanding": remaining(165000, 1.6, 240, paid(dt.date(2018, 6, 1)))}

    for mi, m in enumerate(months):
        y, mo = m.year, m.month
        recent = m >= add_months(today.replace(day=1), -6)
        # ---------------------------------------------------------------- income
        w.tx("nova-anna", day_in(m, 27), 3420.0 + (150 if m >= dt.date(2026, 1, 1) else 0), "SALAIRE NORDWIND SYSTEMS", "transfer_in",
             "VIR SEPA NORDWIND SYSTEMS SALAIRE")
        w.tx("nova-luca", day_in(m, 29), 2780.0, "SALAIRE BELLAVISTA HOTELS", "transfer_in", "VIR SEPA BELLAVISTA HOTELS SALAIRE")
        if mo == 12:
            w.tx("nova-anna", day_in(m, 20), 1800.0, "SALAIRE NORDWIND SYSTEMS", "transfer_in", "VIR SEPA NORDWIND SYSTEMS PRIME ANNUELLE")
        # contributions to the joint account
        w.transfer("nova-anna", "aur-joint", day_in(m, 28), 2750.0, "VIR VERS COMPTE JOINT", "VIR DE ANNA ROSSI")
        w.transfer("nova-luca", "aur-joint", day_in(m, 30), 2150.0, "VIR VERS COMPTE JOINT", "VIR DE LUCA ROSSI", in_date=day_in(m, 30))
        # ---------------------------------------------------------------- housing
        w.tx("aur-joint", day_in(m, 5), -facts["mortgage_payment"], "HOMEBANK ECH PRET", "loan_payment", "PRLV HOMEBANK ECH PRET 0042")
        winter = mo in (11, 12, 1, 2, 3)
        w.tx("aur-joint", day_in(m, 9), -(w.r(128, 172) if winter else w.r(62, 88)), "SUNPOWER ENERGIE", "direct_debit", "PRLV SUNPOWER ENERGIE")
        if mo % 2 == 0:
            w.tx("aur-joint", day_in(m, 14), -w.r(44, 56), "AQUALIS EAUX", "direct_debit", "PRLV AQUALIS EAUX FACTURE")
        w.tx("aur-joint", day_in(m, 2), -(24.80 if m >= dt.date(2026, 1, 1) else 22.50), "HOMESURE ASSURANCES", "direct_debit",
             "PRLV HOMESURE ASSURANCES HABITATION")
        w.tx("aur-joint", day_in(m, 11), -120.0, "CLEANHOME SERVICES", "direct_debit", "PRLV CLEANHOME SERVICES CESU")
        if mo == 10:
            w.tx("aur-joint", day_in(m, 6), -1240.0, "DGFIP TAXE FONCIERE", "direct_debit", f"PRLV DGFIP TAXE FONCIERE {y}")
        if mo == 11:
            w.tx("aur-joint", day_in(m, 15), -312.0, "DGFIP TAXE HABITATION", "direct_debit", f"PRLV DGFIP TAXE HABITATION RS {y}")
        if mi % 3 == 1:
            w.tx("aur-joint", day_in(m, 17), -w.r(35, 140), "BRICOMAX", "card", "CB BRICOMAX")
        # ---------------------------------------------------------------- subscriptions
        w.tx("aur-joint", day_in(m, 10), -29.99, "TELCOCO BOX", "direct_debit", "PRLV TELCOCO BOX FIBRE")
        w.tx("aur-joint", day_in(m, 12), -25.98, "PIXELTEL MOBILE", "direct_debit", "PRLV PIXELTEL MOBILE 2 LIGNES")
        w.tx("nova-anna", day_in(m, 7), -(15.99 if m >= dt.date(2026, 5, 1) else 13.99), "STREAMBOX", "card", "CB STREAMBOX.COM")
        if m >= dt.date(2025, 9, 1):
            w.tx("nova-luca", day_in(m, 19), -8.99, "VIDEOMAX", "card", "CB VIDEOMAX PREMIUM")
        w.tx("aur-joint", day_in(m, 21), -(17.99 if m >= dt.date(2026, 3, 1) else 16.99), "SOUNDWAVE FAMILY", "card", "CB SOUNDWAVE FAMILY")
        w.tx("nova-anna", day_in(m, 6), -(3.99 if m >= dt.date(2026, 2, 1) else 2.99), "CLOUDBOX", "card", "CB CLOUDBOX STORAGE")
        if m < dt.date(2026, 3, 1):
            w.tx("nova-luca", day_in(m, 8), -6.99, "OLDAPP", "card", "CB OLDAPP PRO")
        w.tx("nova-luca", day_in(m, 3), -9.90, "DAILY HERALD", "card", "CB DAILY HERALD NUMERIQUE")
        w.tx("aur-joint", day_in(m, 12), -39.90, "FITCLUB", "direct_debit", "PRLV FITCLUB ABONNEMENT")
        w.tx("aur-joint", day_in(m, 4), -89.40, "MUTUELLE HARMONIA", "direct_debit", "PRLV MUTUELLE HARMONIA")
        w.tx("aur-joint", day_in(m, 16), -15.0, "SOLIDARITE MONDE", "direct_debit", "PRLV SOLIDARITE MONDE DON MENSUEL")
        w.tx("aur-joint", day_in(m, 1), -5.50, "COTISATION CARTE", "bank_fee", "COTISATION CARTE VISA PREMIER")
        # ---------------------------------------------------------------- food
        for wk in range(4):
            w.tx("aur-joint", day_in(m, 3 + wk * 7), -w.r(92, 168), "ACME GROCERS", "card", "CB ACME GROCERS")
        for d in (9, 23):
            w.tx("aur-joint", day_in(m, d), -w.r(28, 61), "FRESH MARKET", "card", "CB FRESH MARKET BIO")
        for d in range(2, 28, 4):
            w.tx("aur-joint", day_in(m, d), -w.r(3.2, 9.8), "BOULANGERIE DU COIN", "card", "CB BOULANGERIE DU COIN")
        w.tx("aur-joint", day_in(m, 13), -w.r(18, 34), "PRIMEUR DU MARCHE", "card", "CB PRIMEUR DU MARCHE")
        w.tx("aur-joint", day_in(m, 15), -w.r(58, 96), w.rnd.choice(["TRATTORIA BELLA", "LE PETIT BISTROT"]), "card")
        w.tx("aur-joint", day_in(m, 26), -w.r(42, 88), "TRATTORIA BELLA", "card", "CB TRATTORIA BELLA")
        w.tx("aur-joint", day_in(m, 20), -w.r(24, 39), "BURGER STATION", "card", "CB BURGER STATION")
        for d in ((8, 22) if recent else (8,)):
            w.tx("aur-joint", day_in(m, d), -w.r(26, 44), "FOODRUN", "card", "CB FOODRUN*COMMANDE")
        for d in range(3, 26, 5):
            w.tx("nova-anna", day_in(m, d), -w.r(8.5, 13.9), "CANTINE ENTREPRISE", "card", "CB CANTINE ENTREPRISE")
            w.tx("nova-luca", day_in(m, d + 1), -w.r(3.1, 6.4), "CAFE DES ARTS", "card", "CB CAFE DES ARTS")
        # ---------------------------------------------------------------- transport
        for d in (7, 21):
            w.tx("aur-joint", day_in(m, d), -w.r(58, 79), "PETROLINE", "card", "CB PETROLINE STATION")
        if mi % 2 == 0:
            w.tx("aur-joint", day_in(m, 18), -w.r(12, 38), "AUTOROUTE DU SUD", "card", "CB AUTOROUTE DU SUD PEAGE")
        w.tx("nova-anna", day_in(m, 1), -86.40, "METROPASS", "direct_debit", "PRLV METROPASS NAVIGO")
        w.tx("aur-joint", day_in(m, 8), -48.20, "AUTOSURE", "direct_debit", "PRLV AUTOSURE AUTO")
        w.tx("aur-joint", day_in(m, 15), -289.0, "MOBILEASE LOA", "loan_payment", "PRLV MOBILEASE LOA LOYER")
        if mo in (4, 10):
            w.tx("aur-joint", day_in(m, 22), -w.r(160, 340), "GARAGE DU PONT", "card", "CB GARAGE DU PONT")
        # ---------------------------------------------------------------- shopping, health, leisure
        w.tx("aur-joint", day_in(m, 11), -w.r(25, 95), "MEGAMART ONLINE", "card", "CB MEGAMART ONLINE")
        if mi % 2:
            w.tx("aur-joint", day_in(m, 24), -w.r(30, 120), "MEGAMART ONLINE", "card", "CB MEGAMART ONLINE")
        w.tx("nova-anna", day_in(m, 14), -w.r(35, 140), "MODA URBANA", "card", "CB MODA URBANA")
        if mo in (3, 9, 11):
            w.tx("nova-luca", day_in(m, 16), -w.r(45, 160), "ATHLETICA", "card", "CB ATHLETICA")
        w.tx("nova-luca", day_in(m, 24), -w.r(12, 34), "LIBRAIRIE DES QUAIS", "card", "CB LIBRAIRIE DES QUAIS")
        w.tx("aur-joint", day_in(m, 10), -w.r(9, 42), "PHARMACIE CENTRALE", "card", "CB PHARMACIE CENTRALE")
        if mi % 3 == 0:
            w.tx("aur-joint", day_in(m, 19), -30.0, "CABINET MEDICAL DES PINS", "card", "CB CABINET MEDICAL DES PINS")
            w.tx("aur-joint", day_in(m, 25), 21.0, "REMB MUTUELLE HARMONIA", "transfer_in", "VIR MUTUELLE HARMONIA REMBOURSEMENT")
        if mi % 2 == 0:
            w.tx("nova-anna", day_in(m, 22), -w.r(38, 72), "SALON LUMIERE", "card", "CB SALON LUMIERE")
        w.tx("aur-joint", day_in(m, 27), -w.r(24, 52), "CINEMA LE ROYAL", "card", "CB CINEMA LE ROYAL")
        if mo in (4, 6, 10):
            w.tx("aur-joint", day_in(m, 12), -w.r(96, 140), "PARC AVENTURA", "card", "CB PARC AVENTURA")
        if mi % 2 == 1:
            w.tx("aur-joint", day_in(m, 13), -w.r(40, 80), "RETRAIT DAB", "atm", "RETRAIT DAB BANQUE AURORE")
        # ---------------------------------------------------------------- kids
        if mo not in (7, 8):
            w.tx("aur-joint", day_in(m, 6), -w.r(88, 104), "CANTINE SCOLAIRE", "direct_debit", "PRLV CANTINE SCOLAIRE MAIRIE")
        if mo in (9, 1, 4):
            w.tx("aur-joint", day_in(m, 9), -210.0, "ECOLE DE MUSIQUE", "direct_debit", "PRLV ECOLE DE MUSIQUE TRIMESTRE")
        if mo == 7:
            w.tx("aur-joint", day_in(m, 4), -460.0, "CENTRE DE LOISIRS", "card", "CB CENTRE DE LOISIRS ETE")
        if mo == 12:
            w.tx("aur-joint", day_in(m, 12), -w.r(180, 260), "TOYLAND", "card", "CB TOYLAND")
            w.tx("nova-anna", day_in(m, 15), -w.r(150, 240), "MAISON DECO", "card", "CB MAISON DECO")
        # pocket money: Anna -> Mia (cross-account, linked), the joint account tops up Noa's prepaid card
        w.transfer("nova-anna", "nova-mia", day_in(m, 5), 30.0, "VIR MIA ROSSI ARGENT DE POCHE", "VIR RECU DE ANNA ROSSI",
                   tx_type="person_transfer_out")
        w.transfer("aur-joint", "pre-noa", day_in(m, 2), 20.0, "RECHARGE CARTE PREPAYEE NOA", "RECHARGE CARTE", tx_type="topup")
        for d in (8, 17, 25):
            w.tx("nova-mia", day_in(m, d), -w.r(2.5, 6.0), "BOULANGERIE DU COIN", "card", "CB BOULANGERIE DU COIN")
        if mi % 2 == 0:
            w.tx("nova-mia", day_in(m, 12), -w.r(9.9, 19.9), "JEUXVIDEO SHOP", "card", "CB JEUXVIDEO SHOP")
        if mo in (3, 6, 10):
            w.tx("nova-mia", day_in(m, 20), -w.r(12, 28), "CINEMA LE ROYAL", "card", "CB CINEMA LE ROYAL")
        if mo in (5, 12):
            w.tx("nova-mia", day_in(m, 18), 50.0, "VIR GRAND MERE CADEAU", "person_transfer_in", "VIR GRAND MERE CADEAU")
        for d in (9, 23):
            w.tx("pre-noa", day_in(m, d), -w.r(3.5, 7.5), "SNACK BAR DU COLLEGE", "card", "CB SNACK BAR DU COLLEGE")
        if mi % 2:
            w.tx("pre-noa", day_in(m, 15), -w.r(6.5, 9.9), "BD ET MANGAS", "card", "CB BD ET MANGAS")
        # ---------------------------------------------------------------- savings
        w.transfer("nova-anna", "aur-livret", day_in(m, 29), 300.0, "VIR VERS LIVRET A", "VIR DE ANNA ROSSI EPARGNE")
        w.transfer("nova-luca", "aur-livret", day_in(m, 30), 250.0, "VIR VERS LIVRET A", "VIR DE LUCA ROSSI EPARGNE")
        w.transfer("aur-joint", "aur-livret", day_in(m, 3), 400.0, "VIR PERMANENT VERS LIVRET A", "VIR PERMANENT DEPUIS COMPTE JOINT")
        if mo == 12:
            w.tx("aur-livret", day_in(m, 31), round(14000 * 0.024, 2), "INTERETS LIVRET", "transfer_in", "INTERETS ANNUELS LIVRET A")
        # ---------------------------------------------------------------- travel
        if mo == 7:
            w.transfer("aur-livret", "aur-joint", day_in(m, 1), 2500.0, "VIR VERS COMPTE JOINT VACANCES", "VIR DEPUIS LIVRET A")
            w.tx("aur-joint", day_in(m, 2), -w.r(780, 920), "AIRVOLA", "card", "CB AIRVOLA BILLETS")
            w.tx("aur-joint", day_in(m, 18), -w.r(1350, 1650), "HOTEL MIRAMARE", "card", "CB HOTEL MIRAMARE")
            w.tx("aur-joint", day_in(m, 19), -w.r(240, 320), "RENTACAR", "card", "CB RENTACAR")
            w.tx("aur-joint", day_in(m, 24), -w.r(85, 140), "KIOSQUE PLAGE", "card", "TPE 0457 KIOSQUE PLAGE")
        if mo == 2:
            w.tx("aur-joint", day_in(m, 14), -w.r(1100, 1300), "CHALET DES CIMES", "card", "CB CHALET DES CIMES")
            w.tx("aur-joint", day_in(m, 13), -w.r(280, 360), "RAILEXPRESS", "card", "CB RAILEXPRESS")
        # ---------------------------------------------------------------- rental flat
        vacancy = (y, mo) == (2025, 9)
        if not vacancy:
            w.tx("hor-rental", day_in(m, 3), 720.0, "LOYER LOCATAIRE APPT 2B", "transfer_in", "VIR LOYER LOCATAIRE APPT 2B")
            w.tx("hor-rental", day_in(m, 4), -50.40, "GESTION LOCATIVE AGENCE ALPHA", "direct_debit", "PRLV FRAIS DE GESTION LOCATIVE AGENCE ALPHA")
        w.tx("hor-rental", day_in(m, 1), -rental_loan, "LENDERCO ECH PRET", "loan_payment", "ECH PRET LENDERCO IMMO")
        w.tx("hor-rental", day_in(m, 10), -14.20, "ASSURANCE PNO PROPRIO", "direct_debit", "PRLV ASSURANCE PNO PROPRIO")
        if mo in (1, 4, 7, 10):
            w.tx("hor-rental", day_in(m, 5), -186.0, "SYNDIC RESIDENCE LES TILLEULS", "direct_debit", "PRLV SYNDIC RESIDENCE LES TILLEULS")
        if mo == 10:
            w.tx("hor-rental", day_in(m, 12), -980.0, "DGFIP TAXE FONCIERE", "direct_debit", f"PRLV DGFIP TAXE FONCIERE {y} APPT")
        w.tx("hor-rental", day_in(m, 28), -2.90, "COTISATION CARTE", "bank_fee", "F TENUE DE COMPTE")
        w.transfer("aur-joint", "hor-rental", day_in(m, 26), 300.0 if mo != 10 else 1300.0, "VIR VERS COMPTE LOCATION", "VIR DEPUIS COMPTE JOINT")

    # ---------------------------------------------------------------- one-offs and the review queue
    reno = dt.date(2026, 3, 1)
    if reno < today:
        w.transfer("aur-livret", "aur-joint", day_in(reno, 9), 8000.0, "VIR VERS COMPTE JOINT TRAVAUX", "VIR DEPUIS LIVRET A")
        w.tx("aur-joint", day_in(reno, 10), -4200.0, "BRICO RENOV SARL", "transfer_out", "VIR BRICO RENOV SARL ACOMPTE CUISINE")
        w.tx("aur-joint", day_in(reno, 30), -4200.0, "BRICO RENOV SARL", "transfer_out", "VIR BRICO RENOV SARL SOLDE CUISINE")
    w.tx("nova-luca", today - dt.timedelta(days=24), -1299.0, "TECHNOPOLIS", "card", "CB TECHNOPOLIS ORDINATEUR")
    w.tx("aur-joint", today - dt.timedelta(days=11), -64.90, "MEGAMART ONLINE", "card", "CB MEGAMART ONLINE")
    w.tx("aur-joint", today - dt.timedelta(days=10), -64.90, "MEGAMART ONLINE", "card", "CB MEGAMART ONLINE")      # a duplicate charge
    w.tx("aur-joint", today - dt.timedelta(days=33), -w.r(180, 220), "OPTIQUE VISION", "card", "CB OPTIQUE VISION")
    for i, (key, amt) in enumerate([("SQ LA FABRIQUE", 46.0), ("PAYPAL GREENLEAF", 72.5), ("MARCHE NOCTURNE", 38.0), ("ZEN SPA", 95.0),
                                    ("SUMUP CHEZ NINA", 27.5)]):
        w.tx("aur-joint" if i % 2 else "nova-anna", today - dt.timedelta(days=5 + 9 * i), -amt, key, "card", f"CB {key}")
    w.tx("aur-joint", today - dt.timedelta(days=19), -150.0, "VIR INST M LEBRUN", "person_transfer_out", "VIR INST M LEBRUN COVOITURAGE")
    return facts


# ------------------------------------------------------------------------------------------------------------------ memory

def memory_files(today: dt.date, facts: dict) -> dict[str, str]:
    iso = lambda d: d.isoformat()                                                    # noqa: E731
    month_ago = add_months(today, -1)
    return {
        "household.yaml": f"""\
# Demo household (synthetic): who is in it, who owns which account, who a payment belongs to, the kids' limits and who pays what.
members:
  - id: anna
    name: Anna Rossi
    role: adult
    birth_year: 1984
    aliases: ["ANNA ROSSI", "M OU MME ROSSI"]
  - id: luca
    name: Luca Rossi
    role: adult
    birth_year: 1982
    aliases: ["LUCA ROSSI"]
  - id: mia
    name: Mia Rossi
    role: child
    birth_year: 2012
    pocket_money: {{ amount: 30, period: monthly, day: 5 }}
  - id: noa
    name: Noa Rossi
    role: child
    birth_year: 2015
    pocket_money: {{ amount: 20, period: monthly, day: 2 }}

attribution:
  - id: noa-prepaid
    member: noa
    match: {{ account: pre-noa }}
  - id: luca-gym
    member: luca
    match: {{ merchant_key: '^FITCLUB' }}

kid_budgets:
  - id: mia-games
    member: mia
    period: monthly
    limit: 20
    group: leisure
    note: games and cinema
  - id: noa-weekly
    member: noa
    period: weekly
    limit: 8

allocations:
  - id: groceries
    title: Groceries
    match: {{ category: food.groceries }}
    method: equal
  - id: housing
    title: Housing
    match: {{ group: housing }}
    method: income
  - id: kids
    title: The kids
    match: {{ group: kids }}
    method: custom
    shares: {{ anna: 60, luca: 40 }}
""",
        "assets.yaml": f"""\
# Assets the bank sync does not see (synthetic)
assets:
  - id: family-house
    kind: real_estate
    description: Family house
    value: 445000
    as_of: {iso(add_months(today.replace(day=1), -24))}
    purchase_price: 365000
    purchase_date: 2019-06-20
  - id: employee-savings
    kind: employee_savings_plan
    provider: Nordwind Epargne
    holder: anna
    balance: 18400
    as_of: {iso(add_months(today, -1))}
    contribution_monthly: 150
  - id: life-insurance
    kind: life_insurance
    provider: Assurvie
    holder: luca
    balance: 32150
    as_of: {iso(add_months(today, -5))}
    liquidity: weeks
  - id: family-car
    kind: vehicle
    description: Leased family car (value of the purchase option)
    value: null
    as_of: null
  - id: rental-flat
    kind: real_estate_rental
    description: Two-room flat let under a tax-incentive scheme
    account: hor-rental
    loan: rental-loan
    scheme: pinel
    value: 212000
    as_of: {iso(add_months(today.replace(day=1), -24))}
    rent_monthly: 720
    purchase_price: 189000
    purchase_date: 2018-05-15
    commitment:
      start_date: 2018-07-01
      years: 9
      surface_m2: 44
      rent_cap_monthly: 735
      tenant_income_limit: 38000
      tenant_income: 31500
      reduction_rate_pct: 18
      reduction_first_year: 2018
    market_rate: {{ rate_pct: 3.35, as_of: {iso(add_months(today, -2))}, source: "broker newsletter" }}
    vacancies:
      - start: 2025-09-01
        end: 2025-09-30
        note: between two tenants
""",
        "liabilities/home-loan.yaml": f"""\
id: home-loan
kind: mortgage
lender: Homebank
asset: family-house
holder: joint
start_date: 2019-07-01
term_months: 300
payment_day: 5
principal: 280000
outstanding: {facts['mortgage_outstanding']}
outstanding_as_of: {facts['statement_date']}        # the January statement
rate: {{ type: fixed, nominal: 1.35, taeg: 1.71 }}
monthly_payment: {facts['mortgage_payment']}
insurance: {{ provider: AssurPret, monthly: 31.5, delegated: false }}
debited_account: aur-joint
payment_match: '^HOMEBANK'
early_repayment_penalty: "3 % of the repaid capital, capped at 6 months of interest"
documents: []
notes: ""
""",
        "liabilities/rental-loan.yaml": f"""\
id: rental-loan
kind: mortgage
lender: Lenderco
asset: rental-flat
holder: joint
start_date: 2018-06-01
term_months: 240
payment_day: 1
principal: 165000
outstanding: {facts['rental_outstanding']}
outstanding_as_of: {facts['statement_date']}
rate: {{ type: fixed, nominal: 1.6 }}
monthly_payment: {facts['rental_loan_payment']}
debited_account: hor-rental
payment_match: 'LENDERCO'
documents: []
notes: ""
""",
        "liabilities/car-lease.yaml": f"""\
id: car-lease
kind: loa
lender: MobiLease
asset: family-car
holder: joint
start_date: 2024-03-01
end_date: 2028-03-01
term_months: 48
payment_day: 15
monthly_payment: 289
first_payment: 3000
residual_value: 14500
mileage_limit_km: 60000
excess_km_fee: 0.08
initial_km: 0
odometer:
  - {{ date: 2025-03-02, km: 16100 }}
  - {{ date: {iso(month_ago)}, km: {16100 + int((month_ago - dt.date(2025, 3, 2)).days * 46)} }}
debited_account: aur-joint
payment_match: '^MOBILEASE'
documents: []
notes: ""
""",
        "contracts/telco.yaml": """\
id: telco
provider: TelcoCo
kind: telecom
holder: anna
merchant_match: '^TELCOCO'
start_date: 2024-02-10
commitment_end: 2026-02-10
notice_period_days: 10
billing: { amount: 29.99, period: monthly }
usage: { frequency: daily, note: "fibre box, everybody uses it" }
contract_number: TC-000123
documents: []
notes: ""
""",
        "contracts/streambox.yaml": f"""\
id: streambox
provider: StreamBox
kind: streaming
holder: anna
merchant_match: '^STREAMBOX'
start_date: 2023-11-07
billing: {{ amount: 15.99, period: monthly }}
usage: {{ frequency: rarely, last_used: {iso(today - dt.timedelta(days=74))}, note: "only the kids, at weekends" }}
keep: review
documents: []
notes: ""
""",
        "contracts/homesure.yaml": f"""\
id: homesure
provider: HomeSure
kind: insurance_home
holder: joint
merchant_match: '^HOMESURE'
start_date: 2019-06-20
renewal: {iso(add_months(today, 2).replace(day=20))}
billing: {{ amount: 24.80, period: monthly }}
usage: {{ frequency: unknown }}
documents: []
notes: ""
""",
        "contracts/fitclub.yaml": f"""\
id: fitclub
provider: FitClub
kind: membership
holder: luca
merchant_match: '^FITCLUB'
start_date: 2023-01-12
commitment_end: {iso(add_months(today, 1).replace(day=12))}
notice_period_days: 30
billing: {{ amount: 39.90, period: monthly }}
usage: {{ frequency: rarely, last_used: {iso(today - dt.timedelta(days=41))} }}
documents: []
notes: ""
""",
        "contracts/sunpower.yaml": """\
id: sunpower
provider: SunPower
kind: energy
holder: joint
merchant_match: '^SUNPOWER'
start_date: 2021-09-01
billing: { period: monthly }
usage: { frequency: daily }
documents: []
notes: ""
""",
        "budgets.yaml": """\
budgets:
  - id: groceries
    category: food.groceries
    monthly: 780
  - id: eating-out
    group: food
    monthly: 1050
    note: everything food, home and out
  - id: restaurants
    category: food.restaurants
    monthly: 150
  - id: delivery
    category: food.food_delivery
    monthly: 40
  - id: fuel
    category: transport.fuel
    monthly: 150
  - id: clothing
    category: shopping.clothing
    monthly: 120
    owner: anna
  - id: leisure
    group: leisure
    monthly: 160
    rollover: true
  - id: kids
    group: kids
    monthly: 260
""",
        "goals.yaml": f"""\
goals:
  - id: emergency-fund
    title: Emergency fund (six months of spending)
    target_amount: 30000
    account: aur-livret
    monthly_contribution: 400
  - id: summer-2027
    title: Summer holiday 2027
    target_amount: 3500
    target_date: {today.year + 1}-06-30
    tag: summer-2027
    monthly_contribution: 300
""",
        "categorization.yaml": f"""\
# Annotations (synthetic): what the bank data alone cannot say
annotations:
  - id: kitchen-works
    match:
      merchant_key: '^BRICO RENOV'
    category: housing.renovation
    tags: [one_off, capital]
    event: kitchen-2026
    note: Kitchen renovation, paid from the savings book.
  - id: new-laptop
    match:
      merchant_key: '^TECHNOPOLIS'
      date_from: {iso(today - dt.timedelta(days=40))}
    tags: [one_off]
    note: Luca's work laptop, half refunded by the employer later.
  - id: summer-trip
    match:
      merchant_key: '^(AIRVOLA|HOTEL MIRAMARE|RENTACAR)'
    tags: [holiday]
    event: summer-holiday
""",
        "events.md": """\
# Events

## kitchen-2026

- **What:** kitchen renovation by a contractor, two payments (deposit and balance).
- **Funding:** the savings book.

## summer-holiday

- **What:** the yearly family trip in July (flights, hotel, car).
""",
        "preferences.md": """\
# Coach preferences

- Language: English. Short answers, a table when there are more than three numbers.
- Tone: friendly and direct, no lecturing.
- Topics: help us keep the emergency fund growing and spot subscriptions we forget.
- Never comment on the kids' pocket money unless asked.
""",
        "profile.md": """\
# Profile (synthetic)

A family of four: two adults who both work, two children at school. One family house with a mortgage, a leased car,
and a small rented flat bought under a tax-incentive scheme.
""",
    }


# ------------------------------------------------------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--home", required=True, type=Path)
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--force", action="store_true", help="rebuild an existing DEMO home")
    a = ap.parse_args()
    home = a.home.expanduser().resolve()
    prepare_home(home, a.port, a.force)

    from coach.config import load_config
    from coach.db import connect

    cfg = load_config()
    assert Path(cfg.db_path).resolve().is_relative_to(home), cfg.db_path
    con = connect(cfg, insecure=True, create=True)
    today = dt.date.today()

    # banks, accounts, consents
    for sid, bank, country, days in SESSIONS:
        valid = dt.datetime.combine(today + dt.timedelta(days=days), dt.time(9), dt.timezone.utc).isoformat()
        con.execute("INSERT INTO sessions(session_id, aspsp_name, aspsp_country, valid_until, created_at, raw, status) VALUES (?,?,?,?,?,?,?)",
                    (sid, bank, country, valid, (today - dt.timedelta(days=180 - days)).isoformat() + "T09:00:00+00:00", "{}", "active"))
    for i, (uid, sid, bank, name, owner, purpose, label, source) in enumerate(ACCOUNTS, 1):
        iban = f"FR76 0000 0000 0000 0000 0000 {i:03d}".replace(" ", "")
        con.execute("INSERT INTO accounts(uid, session_id, name, iban, currency, cash_account_type, raw, bank, owner, purpose, label, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (uid, sid, name, iban, "EUR", "CACC", "{}", bank, owner, purpose, label, source))
    con.commit()

    w = World(con, today)
    facts = build_transactions(w)
    for o, i, amt in w.links:
        con.execute("INSERT INTO transfer_links(out_tx_key, in_tx_key, amount, confidence, method, created_at) VALUES (?,?,?,?,?,?)",
                    (o, i, amt, 1.0, "auto", today.isoformat()))
    for key, (name, cat, conf, src) in M.items():
        con.execute("INSERT OR REPLACE INTO merchants VALUES (?,?,?,?,0,?,?,?)",
                    (key, name, cat, conf, src, "sonnet" if src == "llm" else None, today.isoformat()))
    con.execute("INSERT OR REPLACE INTO merchants VALUES (?,?,?,?,0,?,?,?)",
                ("KIOSQUE PLAGE", "Kiosque plage", "food.cafes_bars", 0.62, "llm", "sonnet", today.isoformat()))

    # balances = opening + every transaction (so the history the net worth back-fills is consistent)
    opening = {"aur-joint": 300, "aur-livret": 4200, "nova-anna": 400, "nova-luca": 650, "nova-mia": 35, "hor-rental": 420, "pre-noa": 6}
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    for uid, base in opening.items():
        s = con.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE account_uid=?", (uid,)).fetchone()[0]
        bal = round(base + s, 2)
        print(f"  balance {uid}: {bal}")
        if bal < 0:
            sys.exit(f"opening balance too low for {uid}: {bal}")
        con.execute("INSERT INTO balances VALUES (?,?,?,?,?,?)", (uid, now.isoformat(), "CLBD", bal, "EUR", today.isoformat()))
    for uid, *_ in ACCOUNTS:
        for d in range(1, 15):
            con.execute("INSERT INTO sync_log(account_uid, ran_at, ok, new_tx, pages, note) VALUES (?,?,?,?,?,?)",
                        (uid, (now - dt.timedelta(days=d)).isoformat(), 1, 3 + d % 5, 1, "demo"))
    con.commit()

    # memory: written through the store, so the change history has entries
    from coach.memory.store import MemoryStore
    store = MemoryStore(cfg.memory_dir, history=True, source="cli")
    files = memory_files(today, facts)
    for rel, text in sorted(files.items(), key=lambda kv: kv[0] == "categorization.yaml"):
        p = Path(cfg.memory_dir) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            store.write_text(rel, text, action="demo-seed", reason="demo household", source="cli")
        except Exception as e:                                            # noqa: BLE001
            print(f"  note: {rel} written directly ({type(e).__name__}: {e})")
            p.write_text(text)

    derived(con, cfg, store, today)
    con.close()
    print(f"demo home ready: {home}\n  start it with: scripts/demo/run_demo.sh {home}")
    print(json.dumps({"transactions": w.n, **facts}, indent=1))


def derived(con, cfg, store, today: dt.date) -> None:
    """Everything the daily job and the users would have produced: analytics, net worth, questions, proposals, decisions,
    alternatives, coach insights, AI usage, the egress journal, the gold set, alerts and the logins."""
    def step(name, fn):
        try:
            out = fn()
            print(f"  {name}: ok")
            return out
        except Exception as e:                                            # noqa: BLE001
            print(f"  {name}: FAILED {type(e).__name__}: {e}")

    from coach.analytics import api as analytics_api
    step("analytics refresh", lambda: analytics_api.refresh_all(con, cfg, today))
    from coach.loans import service as loans_service
    step("net worth history", lambda: loans_service.record_networth(con, cfg, today))

    from coach.memory import questions as Q
    for q, topic, target, stake in [
        ("What is the current value of the family car's purchase option, and do you plan to buy it at the end of the lease?", "Assets",
         "assets.yaml:family-car.value", 14500),
        ("Is 'SUMUP CHEZ NINA' a restaurant, a shop or a person you paid back?", "Categories", None, 27.5),
        ("Does anyone still use the VideoMax subscription? StreamBox covers the same need.", "Subscriptions", None, 107.88),
        ("Who is the borrower insurance of the home loan with, and is it a group contract or a delegated one?", "Loans",
         "liabilities/home-loan.yaml:insurance", 378),
    ]:
        step(f"question ({topic})", lambda q=q, topic=topic, target=target, stake=stake:
             Q.add(store, q, topic=topic, target=target, stake=stake, today=today, source="cli"))
    from coach.memory import proposals as P
    step("proposal 1", lambda: P.create(store, "assets.yaml", [{"op": "set", "path": "life-insurance.balance", "value": 32890},
                                                              {"op": "set", "path": "life-insurance.as_of", "value": today.isoformat()}],
                                        "The yearly statement mentioned in the chat gives a newer value.", "coach-llm"))
    step("proposal 2", lambda: P.create(store, "contracts/streambox.yaml", [{"op": "set", "path": "keep", "value": False}],
                                        "You said nobody watches it during the week: mark it to cancel.", "coach-llm"))

    from coach.subs import alternatives as A, decisions as D
    step("alternative 1", lambda: A.add(con, contract_id="telco", today=today, provider="FibreNet", offer_name="Fibre 1 Gb/s, no TV",
                                        monthly_price=22.99, switching_costs=0, retrieved_at=today - dt.timedelta(days=6),
                                        source_url="https://example.com/fibrenet/offers", method="find-cheaper", source="coach-llm",
                                        features="1 Gb/s, landline included, 12-month price"))
    step("alternative 2", lambda: A.add(con, contract_id="telco", today=today, provider="Hexabox", offer_name="Box Essentiel",
                                        monthly_price=24.99, switching_costs=48, retrieved_at=today - dt.timedelta(days=52),
                                        source_url="https://example.org/hexabox", method="find-cheaper", source="coach-llm",
                                        features="500 Mb/s"))
    step("alternative 3", lambda: A.add(con, contract_id="homesure", today=today, provider="Assura Direct", offer_name="Habitation confort",
                                        monthly_price=19.90, retrieved_at=today - dt.timedelta(days=12), source_url="https://example.net/assura",
                                        method="manual", source="ui"))
    step("decision cancelled", lambda: D.add(con, decision="cancelled", today=today, series_id=_series(con, "OldApp"), name="OldApp",
                                             decided_on=dt.date(2026, 2, 20), effective_on=dt.date(2026, 3, 1), before=6.99, after=0,
                                             note="unused since the switch to CloudBox", source="cli"))
    step("decision proposed", lambda: D.add(con, decision="switched", today=today, contract_id="telco", name="TelcoCo",
                                            before=29.99, after=22.99, note="FibreNet offer, no commitment", source="coach-llm"))

    _coach_history(con, today, step)

    from coach.quality import gold
    step("gold set", lambda: gold.bootstrap(con, cfg))
    import subprocess
    env = dict(os.environ)
    step("eval classify", lambda: subprocess.run([sys.executable, "-m", "coach", "eval", "classify"], env=env, check=True,
                                                 capture_output=True, timeout=300))

    from coach.household import users as U
    from coach.household.people import load as load_people
    people = step("people", lambda: load_people(store))
    if people is not None:
        step("login mia", lambda: U.add(con, people, "mia", "child", "mia"))
        step("login luca", lambda: U.add(con, people, "luca", "adult", "luca"))

    from coach.alerts import engine as AE
    step("alerts", lambda: AE.evaluate(con, cfg, analytics_api.build_dataset(con, cfg, today), cfg.alert_settings))
    con.commit()


def _series(con, key: str):
    r = con.execute("SELECT id FROM recurring_series WHERE group_key=? ORDER BY last_date DESC LIMIT 1", (key,)).fetchone()
    return r[0] if r else None


def _coach_history(con, today: dt.date, step) -> None:
    """Past coach activity: insights (digests, reviews, answers) with evidence, their usage rows and the egress journal."""
    from coach.agent import insights as I
    refs = [r[0] for r in con.execute("SELECT tx_key FROM transactions ORDER BY booking_date DESC LIMIT 400")]
    import hashlib
    h = lambda k: "h_" + hashlib.sha1(k.encode()).hexdigest()[:10]                  # noqa: E731
    pick = lambda key: [h(r[0]) for r in con.execute(                               # noqa: E731
        "SELECT t.tx_key FROM transactions t JOIN tx_enriched e USING(tx_key) WHERE e.merchant_key=? ORDER BY booking_date DESC LIMIT 2", (key,))]
    rnd = random.Random(3)
    rows = []
    for d in range(60, 0, -1):
        day = dt.datetime.combine(today - dt.timedelta(days=d), dt.time(7, 31), dt.timezone.utc)
        if d % 7 == 0:
            rows.append((day, "claude-code", "sonnet", "coach-digest", 1, 21000 + rnd.randint(0, 9000), 900 + rnd.randint(0, 600), 0.0))
        if d % 3 == 0:
            rows.append((day, "anthropic-api", "claude-haiku-4-5", "classify", rnd.randint(3, 18), 2400 + rnd.randint(0, 3000),
                         300 + rnd.randint(0, 400), 0.004 + rnd.random() / 200))
        if d % 5 == 0:
            rows.append((day.replace(hour=20), "openai-compatible", "anthropic/claude-sonnet-4.5", "coach-ask", 1,
                         14000 + rnd.randint(0, 8000), 700 + rnd.randint(0, 500), 0.05 + rnd.random() / 25))
    for ts, backend, model, purpose, items, tin, tout, cost in rows:
        con.execute("INSERT INTO llm_usage(ts, backend, model, purpose, items, tokens_in, tokens_out, cost_usd, cost_is_estimate, duration_s) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", (ts.isoformat(), backend, model, purpose, items, tin, tout, round(cost, 4),
                                                       1 if backend == "claude-code" else 0, round(4 + rnd.random() * 30, 1)))
        host = {"claude-code": "api.anthropic.com", "anthropic-api": "api.anthropic.com", "openai-compatible": "openrouter.ai"}[backend]
        con.execute("INSERT INTO egress_journal(ts, kind, host, bytes, purpose, redaction, outcome, reason, web) VALUES (?,?,?,?,?,?,?,?,?)",
                    (ts.isoformat(), f"llm.{backend}", host, tin * 4, purpose, "coarse", "ok", "", 0))
    for d in range(30, 0, -1):
        ts = dt.datetime.combine(today - dt.timedelta(days=d), dt.time(7, 30), dt.timezone.utc)
        con.execute("INSERT INTO egress_journal(ts, kind, host, bytes, purpose, redaction, outcome, reason, web) VALUES (?,?,?,?,?,?,?,?,?)",
                    (ts.isoformat(), "enable_banking", "api.enablebanking.com", 18000 + d * 37, "sync", "none", "ok", "", 0))
    con.commit()

    last_month = add_months(today.replace(day=1), -1)
    items = [
        dict(kind="review", skill="monthly-review", title=f"{last_month:%B %Y}: a calm month, savings above usual",
             body=("Spending stayed close to your usual month. Groceries were a little above it, eating out below. "
                   "The savings book received its standing transfer as every month, and nothing unusual was charged twice.\n\n"
                   "Three actions:\n1. Look at the two video streaming services: they overlap.\n"
                   "2. The fibre box commitment is over: a cheaper offer is on file.\n3. Answer the question about the car lease option."),
             evidence=pick("ACME GROCERS") + pick("VIDEOMAX"), question=None),
        dict(kind="digest", skill="digest-weekly", title="Week in review: one duplicate charge to check",
             body="Two identical MegaMart payments one day apart look like a duplicate. Everything else was in line with a usual week.",
             evidence=pick("MEGAMART ONLINE"), question=None),
        dict(kind="answer", skill=None, title="Why was July expensive?",
             body=("July is the summer holiday: flights, the hotel and a rental car, funded by a transfer from the savings book. "
                   "Without them, July is a usual month."),
             evidence=pick("AIRVOLA") + pick("HOTEL MIRAMARE"), question="Why was July expensive?"),
        dict(kind="finding", skill="subscription-audit", title="StreamBox went up, and it is rarely used",
             body="StreamBox raised its price this spring. By your own record it is used rarely, only at weekends: a candidate to review.",
             evidence=pick("STREAMBOX"), question=None),
    ]
    for it in items:
        step(f"insight {it['kind']}", lambda it=it: I.add(con, kind=it["kind"], title=it["title"], body=it["body"], evidence=it["evidence"],
                                                          skill=it["skill"], backend="claude-code", model="sonnet", question=it["question"],
                                                          data_through=today.isoformat(), ai_generated=True))
    con.commit()


if __name__ == "__main__":
    main()
