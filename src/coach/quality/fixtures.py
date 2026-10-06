"""Real-shaped, fully INVENTED parser fixtures (E12-1): ``coach eval fixtures synth --bank <bank> --out tests/fixtures/<bank>.json``.

The synthesizer reads a local database to learn the SHAPES of the descriptors of one bank (the prefixes and format words of the bank, how
long the fields are, where the dates, amounts, references and foreign-currency lines stand, which fields are padded) and writes
descriptors of that shape that contain nothing taken from the data:

* every word that is not a format word of the bank (a merchant, a person, a town, a creditor ...) is replaced by a word made of invented
  syllables of the same length (a given name by another given name from the shipped list of common ones, so that the person heuristics
  of the parsers still see a person); the same real word always becomes the same invented word, so repeated merchants stay repeated;
* every digit is re-drawn (an amount keeps its number of digits and whether it is round; a date is moved by one global shift so that
  the booking / card / due dates keep their relations);
* an IBAN-like string becomes a test IBAN whose check digits are 00 (never valid);
* references are re-drawn with the same shape; the Fortuneo "<date>T00:00:00-<n>" position references keep their position.

Nothing is emitted that the guard knows to be real: the household terms (members, places, employers), every token of every merchant key,
description, counterparty and account name of the database, and every long digit string. The check runs at generation time against the
database being read, and a leak raises :class:`SynthLeak` BEFORE a file is written. The committed output is also covered by
``tests/test_egress_policy.py`` (a hashed list of real tokens: no file of the repository may carry one).

The output is Enable Banking shaped (what ``coach.ingest.sync`` stores), one entry per transaction with the type the real shape had::

    {"bank", "parser", "synthetic": true, "account": {...}, "household": {...},
     "transactions": [{"tx": {<EB transaction>}, "account_type": "CACC", "expect": {"tx_type": "card"}}, ...]}
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from coach.classify.parsers import parse_tx, parser_name_for
from coach.classify.parsers.common import (FIRST_NAMES, Household, RawTx, household, strip_accents, ddmm_date)
from coach.ingest.fingerprint import is_position_ref

BANKS = {"fortuneo": "Fortuneo", "caisse_epargne": "Caisse d'Epargne Test", "cic": "CIC", "revolut": "Revolut"}
VERSION = 1

# Words that are part of the FORMAT of a descriptor (prefixes, field tags, transfer / card / fee vocabulary, payment processors, titles,
# currencies, public bank names, generic business nouns): they carry the shape and are kept. A name of a merchant, a person or a town is
# never in this list. A word that is also a household term of the database being read is removed from it at generation time.
STRUCTURAL = frozenset("""
CARTE CB ANN ANNULATION RET RETRAIT DAB DAT ESPECES DISTRIBUTEUR AUTOMATE PAIEMENT ACHAT FACTURE FACT REMBOURSEMENT REMB ANNUL
VIR VIREMENT SEPA INST INSTANTANE WERO RECU RECUS EMIS EMISE PERMANENT OCCASIONNEL SCT SDD AVEC
PRLV PRELEVEMENT PRELEV PRLVT ECH ECHEANCE PRET DU REF REFERENCE RUM MANDAT MANDATO MDT ICS NNE E2E NOTPROVIDED
MOTIF MTF OBJET LIB LIBELLE INFO ORDRE DATE BIC IBAN NOM BEN ORD ID RI EREF DE A N
CHEQUE CHQ REMISE CHEQUES FRAIS TENUE COMPTE CPT COTISATION COTIS AGIOS INTERETS INTERET DEBITEURS DEBITEUR COMMISSION FACTURATION
CONVENTION PARTICIP PARTICIPATION ABONNEMENT PACK OFFRE SERVICE SERVICES CONTRAT F TAXE TAXES
CAP IN CAPITAL
COURS TAUX
EUR USD GBP CHF PLN SEK NOK DKK CZK HUF RON BGN JPY CAD AUD NZD TRY AED ISK
M MR MME MLLE OU ET AU AUX LA LE LES DES D L SUR EN DI DEL
PAYLIB LYDIA SATISPAY MOL SUMUP SQ NYX SUNDAY PAYPAL ZTL STRIPE IZ PAYPLUG LYF
TO FROM TOP UP TOPUP EXCHANGED PAYMENT SENT RECEIVED TRANSFER MONEY REQUEST JUNIOR ACCOUNT WITHDRAWAL BY REVOLUT
LIVRET EPARGNE FORTUNEO CIC CAISSE BANQUE BANK CREDIT MUTUELLE ASSURANCE ASSURANCES PUBLIC PUBLIQUES FINANCES TRESOR
SAS SARL SA SASU SCI SNC EURL SRL SPA SCOP COOP ASSOC ASSOCIATION SYNDICAT SYNDIC LTD LIMITED INC GMBH BV NV AG PLC
ECOLE COLLEGE LYCEE UNIVERSITE CLUB MAIRIE COMMUNE GROUPE COMPAGNIE SOCIETE ENTREPRISE ETS AGENCE CABINET CLINIQUE HOPITAL CENTRE
MAISON GARAGE BOULANGERIE PHARMACIE RESTAURANT HOTEL TELECOM ENERGIE ENERGY SOFTWARE HOLDING FONDATION FEDERATION UNION REGIE
LOCATION LEASING ELECTRICITE EAU GAZ GAS INTERNET MOBILE TELEPHONE POSTE IMPOTS AMENDE LOYER IARD VIE SECURITE SOCIALE
SALAIRE PENSION RETRAITE ALLOCATION ALLOCATIONS PRIME REMUNERATION PAIE
SUPERMARCHE MARCHE TABAC PIZZERIA BRASSERIE CAFE BAR PARKING STATION PEAGE TAXI CINEMA LIBRAIRIE FLEURISTE COIFFURE OPTIQUE SPORT
JARDIN BRICOLAGE ANIMALERIE BOUCHERIE POISSONNERIE EPICERIE PRESSE CADEAUX PHOTO TRAVAUX MATERIAUX AUTO
""".split())

_ALNUM = "A-Za-zÀ-ÖØ-öø-ÿ0-9"
_PATTERN = re.compile(
    r"(?P<iban>\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b)"
    r"|(?P<dmy>\b\d{2}/\d{2}/(?:\d{4}|\d{2})\b)"
    r"|(?P<iso>\b\d{4}-\d{2}-\d{2}\b)"
    r"|(?P<dm>\b\d{2}/\d{2}\b)"
    rf"|(?P<tok>[{_ALNUM}]+)")
_VOWELS, _CONSONANTS = "aeiou", "bdfghjklmnprstvz"


class SynthLeak(RuntimeError):
    """The output would contain something the real data contains: nothing is written."""


class SynthError(RuntimeError):
    pass


def fold(text: str) -> str:
    return strip_accents(text or "").upper()


def words_of(text: str) -> set[str]:
    return {fold(w) for w in re.findall(r"[A-Za-zÀ-ÿ]{3,}", text or "")}


def iban_valid(iban: str) -> bool:
    s = re.sub(r"\s", "", iban or "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}", s):
        return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


# ---------------------------------------------------------------- what the database knows (never written out)

@dataclass
class RealTerms:
    """Everything that must not appear in the output. Kept in memory only."""
    forbidden: set = field(default_factory=set)       # alphabetic words (>= 3 letters), accent-stripped, upper case
    digit_runs: set = field(default_factory=set)      # digit strings of 8+ characters
    keys: set = field(default_factory=set)            # merchant keys
    household_terms: set = field(default_factory=set)
    structural: frozenset = STRUCTURAL

    def count(self) -> int:
        return len(self.forbidden) + len(self.digit_runs) + len(self.keys)


def collect_terms(con, cfg) -> tuple[RealTerms, Household]:
    t = RealTerms()
    hh = household(con, cfg.memory_dir)
    terms = set(hh.family) | set(hh.first_names) | {x for h in hh.holders for x in h}
    try:
        from coach.analytics.identity import derive_terms
        from coach.memory.store import MemoryStore
        d = derive_terms(con, MemoryStore(cfg.memory_dir, history=False))
        for x in d.all_terms():
            terms |= words_of(x)
    except Exception:                                                                    # noqa: BLE001
        pass
    texts: list[str] = []
    queries = ("SELECT description, counterparty FROM transactions", "SELECT merchant_key, merchant_raw FROM tx_enriched",
               "SELECT merchant_key, merchant_name FROM merchants", "SELECT name, COALESCE(label,''), COALESCE(owner,''), COALESCE(iban,'') FROM accounts",
               "SELECT description, counterparty FROM pending_transactions")
    for q in queries:
        try:
            for row in con.execute(q):
                texts += [str(x) for x in row if x]
        except Exception:                                                                # noqa: BLE001
            continue
    for x in texts:
        t.forbidden |= words_of(x)
        t.digit_runs |= {m for m in re.findall(r"\d{8,}", x)}
    t.forbidden |= {fold(x) for x in terms if len(x) >= 3}
    t.household_terms = {fold(x) for x in terms if x}
    try:
        t.keys = {r[0] for r in con.execute("SELECT DISTINCT merchant_key FROM tx_enriched WHERE merchant_key <> ''")}
    except Exception:                                                                    # noqa: BLE001
        pass
    # a format word that is ALSO a household term (a given name, a town) is not a format word here
    t.structural = frozenset(w for w in STRUCTURAL if w not in t.household_terms)
    # the format words themselves may be real tokens; they stay allowed, everything else of the real vocabulary is forbidden
    t.forbidden -= set(t.structural)
    return t, hh


# ---------------------------------------------------------------- the mutator

class Mutator:
    def __init__(self, terms: RealTerms, rng: random.Random, shift_days: int):
        self.t, self.rng, self.shift = terms, rng, dt.timedelta(days=shift_days)
        self.words: dict[str, str] = {}
        self.firsts: dict[str, str] = {}
        self.amounts: dict[str, str] = {}
        self.used_words: set[str] = set()
        self.used_refs: set[str] = set()
        self.first_names_real: set[str] = set()
        pool = sorted(n for n in FIRST_NAMES if n not in terms.forbidden and n not in terms.household_terms and n not in terms.structural
                      and len(n) >= 3)
        if len(pool) < 20:
            raise SynthError("too few given names left to invent persons: the shipped list is mostly present in the data")
        self.first_pool = pool
        self._first_free = list(pool)
        rng.shuffle(self._first_free)

    # -- words
    def _syllables(self, n: int) -> str:
        out, vowel = [], self.rng.random() < 0.3
        for _ in range(n):
            out.append(self.rng.choice(_VOWELS if vowel else _CONSONANTS))
            vowel = not vowel
        return "".join(out)

    def invent(self, length: int) -> str:
        for _ in range(60):
            w = self._syllables(length).upper()
            if w not in self.t.forbidden and w not in self.t.structural and w not in FIRST_NAMES and w not in self.used_words:
                self.used_words.add(w)
                return w
        w = self._syllables(length).upper()           # tiny words: the space is small, a repeat is accepted
        if length >= 4 and (w in self.t.forbidden or w in FIRST_NAMES):
            raise SynthLeak(f"could not invent a word of {length} letters")
        return w

    def given_name(self, up: str) -> str:
        if up not in self.firsts:
            same = [n for n in self._first_free if abs(len(n) - len(up)) <= 1]
            pick = same[0] if same else (self._first_free[0] if self._first_free else self.rng.choice(self.first_pool))
            if pick in self._first_free:
                self._first_free.remove(pick)
            self.firsts[up] = pick
        return self.firsts[up]

    def word(self, tok: str) -> str:
        up = fold(tok)
        if up not in self.words:
            if len(up) == 1:
                self.words[up] = self.rng.choice(string.ascii_uppercase)
            elif up in FIRST_NAMES or (up in self.t.household_terms and up in self.first_names_real):
                self.words[up] = self.given_name(up)
            else:
                self.words[up] = self.invent(len(up))
        new = self.words[up]
        if tok.isupper():
            return new
        if tok.islower():
            return new.lower()
        if tok[0].isupper() and tok[1:].islower():
            return new.capitalize()
        return new

    # -- digits, dates, amounts
    def digits(self, s: str) -> str:
        return "".join(self.rng.choice(string.digits) for _ in s)

    def pattern(self, s: str) -> str:
        out = []
        for c in s:
            if c.isdigit():
                out.append(self.rng.choice(string.digits))
            elif c.isalpha():
                letter = self.rng.choice(string.ascii_lowercase)
                out.append(letter.upper() if c.isupper() else letter)
            else:
                out.append(c)
        return "".join(out)

    def amount(self, s: str) -> str:
        """A new amount with the same number of digits; round stays round (rents and instalments), a repeated amount stays repeated."""
        if s in self.amounts:
            return self.amounts[s]
        sign = "-" if s.startswith("-") else ""
        whole, dot, cents = s.lstrip("-").partition(".")
        lo, hi = (0, 9) if len(whole) <= 1 else (10 ** (len(whole) - 1), 10 ** len(whole) - 1)
        new_cents = "00" if cents in ("", "00") else f"{self.rng.randint(1, 99):02d}"
        self.amounts[s] = f"{sign}{self.rng.randint(lo, hi)}" + (f".{new_cents}" if dot else "")
        return self.amounts[s]

    def date(self, iso: str) -> str:
        return (dt.date.fromisoformat(iso[:10]) + self.shift).isoformat()

    def iban(self, s: str) -> str:
        """A test IBAN of the same layout: the country letters, the check digits 00, random characters, redrawn until the mod-97 check fails
        too (so no validator accepts it)."""
        for _ in range(50):
            out, i = [], 0
            for c in s:
                if c.isalnum():
                    i += 1
                    out.append(c if i <= 2 else "0" if i in (3, 4) else (self.rng.choice(string.digits) if c.isdigit() else
                                                                         self.rng.choice(string.ascii_uppercase)))
                else:
                    out.append(c)
            new = "".join(out)
            if not iban_valid(new):
                return new
        raise SynthLeak("could not draw an invalid test IBAN")

    def plain_token(self, t: str) -> str:
        up = fold(t)
        if t.isdigit():
            return self.digits(t)
        if t.isalpha():
            return t if up in self.t.structural else self.word(t)
        if up in self.t.structural:
            return t
        m = re.fullmatch(r"([A-Za-z]+)(\d+)", t)
        if m and fold(m.group(1)) in self.t.structural:
            return m.group(1) + self.digits(m.group(2))
        return self.pattern(t)

    def text(self, s: Optional[str], bdate: Optional[str] = None) -> Optional[str]:
        if not s:
            return s

        def sub(m: re.Match) -> str:
            g = m.lastgroup
            v = m.group(g)
            if g == "iban":
                if len(re.sub(r"\W", "", v)) >= 15 and re.search(r"\d{4}", v):
                    return self.iban(v)
                return _PATTERN.sub(sub2, v)
            if g in ("dmy", "iso", "dm"):
                try:
                    if g == "iso":
                        return self.date(v)
                    if g == "dmy":
                        d, mth, y = v.split("/")
                        new = dt.date(int(y) + (2000 if len(y) == 2 else 0), int(mth), int(d)) + self.shift
                        return f"{new:%d}/{new:%m}/" + (f"{new:%y}" if len(y) == 2 else f"{new:%Y}")
                    real = ddmm_date(v, bdate or "") if bdate else None
                    if real:
                        new = dt.date.fromisoformat(real) + self.shift
                        return f"{new:%d}/{new:%m}"
                except ValueError:
                    pass
                return re.sub(r"\d", lambda x: self.rng.choice(string.digits), v)
            return self.plain_token(v)

        def sub2(m: re.Match) -> str:                     # the pieces of an IBAN-like string that is not one
            return self.plain_token(m.group("tok")) if m.lastgroup == "tok" else m.group(0)
        return _PATTERN.sub(sub, s)

    def reference(self, ref: Optional[str]) -> Optional[str]:
        if not ref:
            return ref
        if is_position_ref(ref):
            m = re.match(r"^(\d{4}-\d{2}-\d{2})(T.*)$", ref)
            return (self.date(m.group(1)) + m.group(2)) if m else self.pattern(ref)
        for _ in range(20):
            new = self.pattern(ref)
            if new not in self.used_refs:
                self.used_refs.add(new)
                return new
        raise SynthLeak("could not draw a unique reference")


# ---------------------------------------------------------------- the fixture

def _bank_accounts(con, bank_key: str) -> list[tuple]:
    rows = con.execute("""SELECT a.uid, COALESCE(a.bank, s.aspsp_name), a.iban, COALESCE(a.cash_account_type,''), s.aspsp_country
                          FROM accounts a LEFT JOIN sessions s ON s.session_id=a.session_id""").fetchall()
    return [r for r in rows if parser_name_for(r[1], r[2], r[4]) == bank_key]


def _real_rows(con, accounts: list[tuple]) -> tuple[list[dict], dict]:
    types = {u: t for u, _, _, t, _ in accounts}
    out, skipped = [], {"other": 0, "unreadable": 0}
    for uid, tx_key, bdate, amount, cur, raw, ttype in con.execute(
            f"""SELECT t.account_uid, t.tx_key, t.booking_date, t.amount, t.currency, t.raw, e.tx_type FROM transactions t
                LEFT JOIN tx_enriched e USING(tx_key) WHERE t.account_uid IN ({','.join('?' * len(types))})
                ORDER BY t.booking_date, t.tx_key""", list(types)):
        try:
            tx = json.loads(raw) if raw else None
        except ValueError:
            tx = None
        if not isinstance(tx, dict) or (not tx.get("remittance_information") and not tx.get("creditor")):
            skipped["unreadable"] += 1
            continue
        if ttype in (None, "other"):
            skipped["other"] += 1
            continue
        out.append({"tx": tx, "type": ttype, "account_type": types[uid], "bdate": bdate, "amount": amount, "currency": cur})
    return out, skipped


def _pick(rows: list[dict], n: int, rng: random.Random) -> list[dict]:
    """n rows: at least 3 of every transaction type present (the rare shapes matter most), the rest at random; with replacement when the
    bank has fewer than n."""
    by_type: dict[str, list] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    chosen: list[dict] = []
    for _t, rs in sorted(by_type.items()):
        chosen += rng.sample(rs, min(3, len(rs)))
    if len(rows) >= n:
        taken = {id(r) for r in chosen}
        rest = [r for r in rows if id(r) not in taken]
        rng.shuffle(rest)
        chosen += rest[:max(0, n - len(chosen))]
    else:
        chosen += [rng.choice(rows) for _ in range(max(0, n - len(chosen)))]
    return chosen


def _amount_str(tx: dict) -> str:
    return str((tx.get("transaction_amount") or {}).get("amount", "0"))


def _build_entry(m: Mutator, real: dict, bank_key: str, pending: bool) -> dict:
    tx = real["tx"]
    bdate = tx.get("booking_date") or real["bdate"]
    ta = tx.get("transaction_amount") or {}
    out: dict = {
        "entry_reference": m.reference(tx.get("entry_reference")),
        "booking_date": m.date(bdate) if bdate else None,
        "value_date": m.date(tx["value_date"]) if tx.get("value_date") else None,
        "status": "PDNG" if pending else "BOOK",
        "transaction_amount": {"amount": m.amount(_amount_str(tx)), "currency": ta.get("currency", "EUR")},
        "credit_debit_indicator": tx.get("credit_debit_indicator"),
        "creditor": {"name": m.text((tx.get("creditor") or {}).get("name"))},
        "debtor": {"name": m.text((tx.get("debtor") or {}).get("name"))},
        "remittance_information": [m.text(x, bdate) for x in (tx.get("remittance_information") or [])],
    }
    if bank_key == "revolut":
        code = ((tx.get("bank_transaction_code") or {}) if isinstance(tx.get("bank_transaction_code"), dict) else {}).get("code")
        if isinstance(code, str) and re.fullmatch(r"[A-Z_]{2,30}", code):
            out["bank_transaction_code"] = {"code": code, "description": None, "sub_code": None}
        inst = ((tx.get("exchange_rate") or {}).get("instructed_amount") or {}) if isinstance(tx.get("exchange_rate"), dict) else {}
        if inst.get("currency") and inst.get("currency") != ta.get("currency"):
            out["exchange_rate"] = {"instructed_amount": {"amount": m.amount(str(inst.get("amount", "0"))), "currency": inst["currency"]}}
    return out


def synthesize(con, cfg, bank_key: str, n: int = 200, seed: int = 12, pending_ratio: float = 0.03) -> tuple[dict, dict]:
    """(fixture, report). The report holds counts only. Raises SynthLeak when something real would be written."""
    if bank_key not in BANKS:
        raise SynthError(f"unknown bank {bank_key!r}; choose one of {', '.join(BANKS)}")
    accounts = _bank_accounts(con, bank_key)
    if not accounts:
        raise SynthError(f"the database has no account handled by the {bank_key} parser")
    rows, skipped = _real_rows(con, accounts)
    if not rows:
        raise SynthError(f"no usable {bank_key} transaction (skipped: {skipped})")
    terms, real_house = collect_terms(con, cfg)
    rng = random.Random(seed)
    shift = -7 * rng.randint(60, 110)                       # a whole number of weeks: the weekdays of card payments stay plausible
    m = Mutator(terms, rng, shift)
    m.first_names_real = {fold(x) for x in real_house.first_names}
    # the household first: the same real name becomes the same invented name everywhere (holders, descriptors)
    for t in sorted(real_house.family | {x for h in real_house.holders for x in h}):
        m.word(t)
    house = Household(
        holders=[{m.word(t) for t in h} for h in real_house.holders],
        family={m.word(t) for t in real_house.family}, first_names={m.word(t) for t in real_house.first_names},
        own_banks={t: b for t, b in real_house.own_banks.items() if t in terms.structural or t in STRUCTURAL})
    picked = _pick(rows, n, rng)
    entries, dropped = [], {"unstable": 0, "other": 0}
    pend_n = 0
    for real in picked:
        built = None
        for attempt in range(6):
            pend = rng.random() < pending_ratio and real["type"] in ("card",) and attempt == 0
            tx = _build_entry(m, real, bank_key, pend)
            amount = float(tx["transaction_amount"]["amount"]) * (-1 if tx["credit_debit_indicator"] == "DBIT" else 1)
            desc = " | ".join(tx["remittance_information"])
            party = (tx["creditor"] if amount < 0 else tx["debtor"]) or {}
            parsed = parse_tx(RawTx(desc, amount, tx["booking_date"] or "", tx["transaction_amount"]["currency"], party.get("name") or "",
                                    ((tx.get("bank_transaction_code") or {}).get("code") or ""), real["account_type"], BANKS[bank_key], tx),
                              house, bank=BANKS[bank_key])
            if pend:                                             # a pending payment is not parsed (it is not stored as a transaction)
                built = (tx, pend, real["type"])
                break
            if parsed["tx_type"] == real["type"]:
                built = (tx, False, real["type"])
                break
            if parsed["tx_type"] == "other":
                dropped["other"] += 1
        if built is None:
            dropped["unstable"] += 1
            continue
        tx, pend, ttype = built
        pend_n += int(pend)
        entries.append({"tx": tx, "account_type": real["account_type"] or "CACC", "expect": {"tx_type": ttype}})
    entries.sort(key=lambda e: (e["tx"]["booking_date"] or "", e["tx"]["entry_reference"] or ""))
    holder = sorted(house.holders[0]) if house.holders else ["TESTA", "OWNER"]
    fixture = {
        "bank": BANKS[bank_key], "parser": bank_key, "synthetic": True, "generator": "coach eval fixtures synth", "version": VERSION, "seed": seed,
        "account": {"name": "M OU MME " + " ".join(holder), "iban": m.iban(f"FR{'76'}{'0' * 23}"), "country": "LT" if bank_key == "revolut" else "FR",
                    "currency": "EUR"},
        "household": {"holders": [sorted(h) for h in house.holders], "family": sorted(house.family), "first_names": sorted(house.first_names),
                      "own_banks": house.own_banks},
        "transactions": entries}
    fixture["account"]["iban"] = _test_iban(rng, fixture["account"]["country"])
    leaks = check_output(fixture, terms)
    report = {"bank": bank_key, "requested": n, "kept": len(entries), "pending": pend_n, "dropped": dropped, "skipped_real": skipped,
              "types": _type_counts(entries), "terms_checked": terms.count(), "leaks": len(leaks)}
    if leaks:
        raise SynthLeak("the output would contain real data (" + str(len(leaks)) + " finding(s): kinds "
                        + ", ".join(sorted({k for k, _ in leaks})) + "): nothing written")
    return fixture, report


def _test_iban(rng: random.Random, country: str) -> str:
    n = {"FR": 23, "LT": 16}.get(country, 18)
    for _ in range(50):
        iban = f"{country}00" + "".join(rng.choice(string.digits) for _ in range(n))
        if not iban_valid(iban):
            return iban
    raise SynthLeak("could not draw an invalid test IBAN")


def _type_counts(entries: list[dict]) -> dict:
    out: dict[str, int] = {}
    for e in entries:
        out[e["expect"]["tx_type"]] = out.get(e["expect"]["tx_type"], 0) + 1
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- the check

def _walk_strings(o):
    """The string VALUES of a structure (the keys are the format of the file, not data)."""
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _walk_strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_strings(v)


def _text_fields(fixture: dict):
    """The strings whose WORDS are checked: descriptors, counterparty names, the account holder and the household. (References are random
    shapes: only their digit runs are checked.)"""
    yield from _walk_strings(fixture.get("account", {}).get("name"))
    yield from _walk_strings(fixture.get("household", {}))
    for e in fixture.get("transactions", []):
        tx = e["tx"]
        yield from _walk_strings(tx.get("remittance_information"))
        yield from _walk_strings((tx.get("creditor") or {}).get("name"))
        yield from _walk_strings((tx.get("debtor") or {}).get("name"))


def check_output(fixture: dict, terms: RealTerms) -> list[tuple[str, str]]:
    """Findings as (kind, ""): a real word, a household term, a real merchant key, a long digit string of the data, a valid IBAN. The
    offending text is never repeated. Empty = clean."""
    found: list[tuple[str, str]] = []
    for s in _text_fields(fixture):
        for w in words_of(s):
            if w in terms.household_terms:
                found.append(("household_term", ""))
            elif w in terms.forbidden:
                found.append(("real_word", ""))
        up = " " + " ".join(re.findall(r"[A-Z0-9]+", fold(s))) + " "
        for k in terms.keys:
            if len(k) >= 4 and f" {k} " in up and not all(w in terms.structural for w in k.split()):
                found.append(("merchant_key", ""))
                break
    for s in _walk_strings(fixture):
        if any(run in terms.digit_runs for run in re.findall(r"\d{8,}", s)):
            found.append(("real_digits", ""))
        for cand in re.findall(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b", s):
            if iban_valid(cand):
                found.append(("valid_iban", ""))
    return found


def write(fixture: dict, out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=1, ensure_ascii=True) + "\n")
    return out
