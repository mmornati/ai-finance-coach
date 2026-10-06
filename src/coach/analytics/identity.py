"""Quasi-identifiers a model must not learn (E6 review): the employer, the work and home towns, and the ids of memory items.

* :func:`derive_terms` finds the employer from the SALARY counterparties and from transactions tagged ``work`` (benefit
  platforms), and the places from ``household.yaml``, from a "work / home area towns" list in ``profile.md`` and from the city
  suffix of merchants tagged ``work`` / ``home``. The redactor masks them and the privacy guard refuses outputs holding them.
* :class:`IdMap` gives every memory item (annotation, asset, liability, contract, event, goal, budget, member) a stable
  pseudonym (``asset-2``) in everything a model sees, with the reverse map kept locally: ``memory_propose`` accepts the
  pseudonyms and maps them back on the server.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

GENERIC_PAY = {"VIR", "VIREMENT", "SEPA", "INST", "SALAIRE", "PAIE", "PAYE", "REMUNERATION", "REM", "DE", "DU", "LA", "LE", "LES", "M",
               "MME", "MR", "MONSIEUR", "MADAME", "FACTURE", "REF", "EUR", "SALARY", "PAYROLL", "WAGES", "FROM", "TO", "DES", "ET",
               "NET", "MOIS", "ACOMPTE", "PRIME", "INDEMNITE", "NOTE", "FRAIS", "SALAIRES"}
PLACE_LINE = re.compile(r"(?i)(?:work|home|travail|domicile|bureau|office)[^:\n]{0,40}(?:towns?|areas?|villes?|communes?|cities|city)"
                        r"[^:\n]{0,20}[:\-–]\s*(.+)")
GENERIC_SUFFIX = {"CARD", "CARTE", "PAIEMENT", "PAYMENT", "DRIVE", "ONLINE", "SHOP", "STORE", "MARKET", "PARIS_", "FRANCE", "SERVICE",
                  "SERVICES", "PRELEVEMENT", "VIREMENT", "ABONNEMENT", "TICKET", "TICKETS", "RESTO", "FACTURE"}
WORK_TAGS = {"work", "travail", "professional"}
HOME_TAGS = {"home", "domicile"}


def fold(s: str) -> str:
    """THE normaliser of the privacy layer (guard and redactor): lower case, accents removed."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def fold_with_map(s: str) -> tuple[str, list[int]]:
    """fold(s) and, for each character of it, the index in `s` it came from (folding can change the length)."""
    out, idx = [], []
    for i, ch in enumerate(s):
        for c in unicodedata.normalize("NFD", ch.lower()):
            if unicodedata.category(c) != "Mn":
                out.append(c)
                idx.append(i)
    return "".join(out), idx


def mask_folded(s: str, rx: "re.Pattern", repl) -> str:
    """Replace every match of `rx` (written on FOLDED text) in `s`, whatever the case and accents of the original. `repl` is a
    string or a function of the folded match."""
    if not s:
        return s
    folded, idx = fold_with_map(s)
    spans = [(m.start(), m.end(), m) for m in rx.finditer(folded)]
    if not spans:
        return s
    out, last = [], 0
    for a, b, m in spans:
        oa, ob = idx[a], idx[b - 1] + 1
        if oa < last:
            continue
        out.append(s[last:oa])
        out.append(repl(m.group(0)) if callable(repl) else repl)
        last = ob
    out.append(s[last:])
    return "".join(out)


def term_regex(terms) -> Optional["re.Pattern"]:
    ts = sorted({fold(t) for t in terms if t and len(fold(t)) >= 3}, key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in ts) + r")(?![a-z0-9])") if ts else None


STREET_WORDS = {"rue", "avenue", "boulevard", "chemin", "route", "impasse", "allee", "allée", "place", "quai", "cours", "residence",
                "résidence", "batiment", "bâtiment", "appartement", "bis", "ter", "via", "viale", "corso", "piazza", "strada", "vicolo",
                "largo", "scala", "interno", "street", "road", "lane", "drive", "apartment", "flat", "france", "italia", "italy"}


def contact_terms(hh) -> list[str]:
    """E8-5: the values of the household's ``contact`` block (postal address, e-mail, phone) as terms the guard refuses and the
    redactor masks: every address line, its words (not the generic street words), the postal codes, the e-mail, the phone."""
    c = hh.get("contact") if isinstance(hh, dict) else None
    if not isinstance(c, dict):
        return []
    out: list[str] = []
    addr = c.get("address")
    if isinstance(addr, str):
        for line in addr.splitlines():
            if len(line.strip()) >= 4:
                out.append(line.strip())
        for tok in re.split(r"[^\w']+", addr):
            if len(tok) >= 4 and fold(tok) not in STREET_WORDS and not tok.isdigit() or re.fullmatch(r"\d{4,6}", tok or ""):
                out.append(tok)
    for key in ("email", "phone"):
        v = c.get(key)
        if isinstance(v, str) and len(v.strip()) >= 5:
            out.append(v.strip())
            digits = re.sub(r"\D", "", v)
            if key == "phone" and len(digits) >= 7:
                out.append(digits)
    return out


def guard_terms(store, con, extra=()) -> set:
    """Every folded term the privacy guard refuses (members, aliases, holders, declared and derived employers / places / schools,
    account labels / names / uids / ibans). The redactor sweeps the same set, so redactor output always passes the guard."""
    from coach.memory.context import STOP
    terms: set[str] = set()

    def add(t, minlen=3):
        t = (t or "").strip()
        if len(t) >= minlen and fold(t) not in STOP:
            terms.add(fold(t))
    try:
        members = store.members()
    except Exception:                                                      # noqa: BLE001
        members = []
    for m in members:
        add(m.name, 3)
        for tok in re.split(r"[^\w']+", m.name):
            add(tok, 3)
        for a in m.aliases:
            add(a, 4)
            for tok in re.split(r"[^\w']+", a):
                add(tok, 4)
        if m.id and len(m.id) >= 4:
            add(m.id, 4)
    try:
        hh = store.load_plain("household.yaml") if store.exists("household.yaml") else {}
    except Exception:                                                      # noqa: BLE001
        hh = {}
    for field_ in ("employers", "places", "schools"):
        for name in hh.get(field_) or []:
            if isinstance(name, str):
                add(name, 3)
    for t in contact_terms(hh):                                            # E8-5: the letters' address / e-mail / phone
        add(t, 4)
    try:
        for _rel, c in store.contracts():                                  # E8-5: contract numbers are printed on letters, never shown to a model
            if getattr(c, "contract_number", None):
                add(str(c.contract_number), 4)
    except Exception:                                                      # noqa: BLE001
        pass
    if con is not None:
        try:
            for (owner,) in con.execute("SELECT DISTINCT owner FROM accounts WHERE owner IS NOT NULL"):
                if owner and owner.lower() != "joint":
                    add(owner, 3)
                    for tok in re.split(r"[^\w']+", owner):
                        add(tok, 3)
            for label, name, uid, iban, api_uid in con.execute("SELECT label, name, uid, iban, api_uid FROM accounts"):
                for text in (label, name):
                    if text and len(text.strip()) >= 5:
                        terms.add(fold(text.strip()))
                for ident in (uid, iban, api_uid):
                    if ident and len(ident) >= 8:
                        terms.add(fold(ident))
        except Exception:                                                  # noqa: BLE001
            pass
        try:
            from coach.classify.candidates import household_names
            fam, first = household_names(con)
            for t in set(fam) | set(first):
                add(t, 3)
        except Exception:                                                  # noqa: BLE001
            pass
    for t in extra:
        add(t, 3)
    return terms


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


@dataclass
class Derived:
    employers: list = field(default_factory=list)       # salary payers, benefit platforms (work-tagged merchants)
    places: list = field(default_factory=list)           # declared + found towns

    def all_terms(self) -> list:
        return list(dict.fromkeys(self.employers + self.places))


def _name_terms(name: str) -> list[str]:
    from coach.classify.candidates import LEGAL_FORMS
    from coach.classify.parsers.common import ORG_WORDS
    name = re.sub(r"\([^)]*\)", " ", name or "")
    words = [w for w in re.findall(r"[A-Za-z0-9&'À-ſ-]+", strip_accents(name).upper())]
    kept = [w for w in words if w not in GENERIC_PAY and w not in ORG_WORDS and w not in LEGAL_FORMS and not w.isdigit() and len(w) >= 3]
    return [" ".join(kept)] if kept else []          # the whole name only: one word of it may be an ordinary word elsewhere


def derive_terms(con, store, ds=None, declared: Optional[dict] = None) -> Derived:
    d = Derived()
    emp: list[str] = []
    places: list[str] = []
    hh = declared if declared is not None else {}
    if not hh and store is not None:
        try:
            hh = store.load_plain("household.yaml") if store.exists("household.yaml") else {}
        except Exception:                                                  # noqa: BLE001
            hh = {}
    for p in hh.get("places") or []:
        if isinstance(p, str) and p.strip():
            places.append(p.strip())
    for e in hh.get("employers") or []:
        if isinstance(e, str) and e.strip():
            emp.append(e.strip())
    if con is not None:
        try:
            for key, name in con.execute("SELECT merchant_key, merchant_name FROM merchants WHERE category='income.salary'"):
                emp += _name_terms(name or "") + _name_terms(key or "")
        except Exception:                                                  # noqa: BLE001
            pass
    suffixes: dict = {}
    if ds is not None:
        for t in ds.whole:
            last = (t.mkey or "").split()[-1:] or [""]
            if last[0].isalpha() and len(last[0]) >= 4:
                suffixes.setdefault(last[0].upper(), set()).add(t.mkey)
        seen_n = 0
        for t in ds.whole:
            if t.category == "income.salary" and seen_n < 5000:
                emp += _name_terms(t.entity or "") + _name_terms(t.mkey or "")
                seen_n += 1
            tags = {x.lower() for x in t.tags}
            if tags & WORK_TAGS and (t.category.startswith("income.") or "reimbursable" in tags or t.amount_c > 0):
                nt = _name_terms(t.entity or t.mkey or "")[:1]             # a benefit platform / reimbursement payer, NOT an ordinary shop
                emp += nt + [w for w in nt[0].split()[:1] if len(w) >= 5] if nt else []
            if tags & (WORK_TAGS | HOME_TAGS):
                last = (t.mkey or "").split()[-1:] or [""]
                if last[0].isalpha() and len(last[0]) >= 4 and last[0].upper() not in GENERIC_SUFFIX \
                        and len(suffixes.get(last[0].upper(), ())) >= 2:        # a town: the suffix of several different merchants
                    places.append(last[0])
    if store is not None:
        try:
            for ln in store.read_text("profile.md").splitlines():
                m = PLACE_LINE.search(ln)
                if m:
                    places += [x for x in (y.strip(" .*_`") for y in re.split(r"[,;/]| et | and ", m.group(1)))
                               if len(x) >= 3 and re.fullmatch(r"[^\W\d_][^\W\d_ '\u2019-]*(?:[ '\u2019-][^\W\d_]+){0,3}", x)]
        except Exception:                                                  # noqa: BLE001
            pass
    from coach.classify.parsers.common import FIRST_NAMES, ORG_WORDS
    d.employers = list(dict.fromkeys(x for x in emp if len(x) >= 3))
    d.places = list(dict.fromkeys(p for p in places if len(p) >= 3 and p.upper() not in ORG_WORDS and p.upper() not in FIRST_NAMES))
    return d


# ---------------------------------------------------------------- memory item ids

class IdMap:
    """real memory id -> stable pseudonym, both ways. Built from the memory (sorted ids per kind)."""

    KINDS = (("annotation", "annotations"), ("asset", "assets"), ("goal", "goals"), ("budget", "budgets"))

    def __init__(self, store, member_pseudo: Optional[dict] = None):
        self.fwd: dict[str, str] = {}
        groups: dict[str, set] = {}
        for kind, meth in self.KINDS:
            try:
                groups[kind] = {x.id for x in getattr(store, meth)()}
            except Exception:                                              # noqa: BLE001
                groups[kind] = set()
        for kind, meth in (("liability", "liabilities"), ("contract", "contracts")):
            try:
                groups[kind] = {m.id for _, m in getattr(store, meth)()}
            except Exception:                                              # noqa: BLE001
                groups[kind] = set()
        # a file that does not validate is not in store.liabilities(): its name still gets a pseudonym, so the real id never reaches a model
        # (question text, an error message)
        from pathlib import Path
        for kind, sub_dir in (("liability", "liabilities"), ("contract", "contracts")):
            d = Path(getattr(store, "root", "")) / sub_dir
            if d.is_dir():
                try:
                    valid = {Path(rel).stem for rel, _m in getattr(store, sub_dir)()}
                except Exception:                                          # noqa: BLE001
                    valid = set()
                groups[kind] |= {p.stem for p in d.glob("*.yaml") if not p.stem.startswith("_") and p.stem not in valid}
        try:
            groups["event"] = set(store.event_ids())
        except Exception:                                                  # noqa: BLE001
            groups["event"] = set()
        for kind, ids in groups.items():
            for n, i in enumerate(sorted(ids), 1):
                if i not in self.fwd:
                    self.fwd[i] = f"{kind}-{n}"
        for real, ps in (member_pseudo or {}).items():
            if real != ps:
                self.fwd[real] = ps
        self.rev = {v: k for k, v in self.fwd.items()}
        ids = sorted((i for i in self.fwd if len(i) >= 3), key=len, reverse=True)
        self._frx = re.compile(r"(?<![\w.-])(" + "|".join(re.escape(i) for i in ids) + r")(?![\w-])(?!\.(?!yaml\b|md\b)\w)") if ids else None
        pss = sorted(self.rev, key=len, reverse=True)
        self._rrx = re.compile(r"(?<![\w-])(" + "|".join(re.escape(i) for i in pss) + r")(?![\w-])") if pss else None

    def forward_text(self, s: str) -> str:
        return self._frx.sub(lambda m: self.fwd[m.group(1)], s) if self._frx and s else s

    def reverse_text(self, s: str) -> str:
        return self._rrx.sub(lambda m: self.rev[m.group(1)], s) if self._rrx and isinstance(s, str) else s

    def forward(self, obj):
        if isinstance(obj, str):
            return self.forward_text(obj)
        if isinstance(obj, dict):
            return {k: self.forward(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.forward(v) for v in obj]
        return obj

    def reverse(self, obj):
        if isinstance(obj, str):
            return self.reverse_text(obj)
        if isinstance(obj, dict):
            return {k: self.reverse(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.reverse(v) for v in obj]
        return obj
