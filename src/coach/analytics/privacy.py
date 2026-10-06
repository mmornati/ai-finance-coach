"""Redaction at the boundary where analytics results leave for a model (P1; the finance MCP server of E6 uses ONLY this).

``redacted_registry(con, cfg)`` returns the same read-only tools as :func:`coach.analytics.api.registry`, but every
result is walked and rewritten before it is returned:

* accounts and owners become stable pseudonyms (``account-main-1``, ``account-kids-2``; ``joint``, ``adult-1``,
  ``kid-1``): the real labels, uids and owner values never appear. The reverse map stays local (``Redactor.reverse``).
* transaction keys and account uids are replaced by a stable hash (``h_`` + 10 hex); series / anomaly / change ids
  (``rec_`` / ``anm_`` / ``chg_``) are already hashes and are kept.
* every free-text value goes through the household scrubber (member names, aliases, holder-name spellings, names
  known from the accounts, common given names) and the technical redaction (IBAN, e-mail, phone, long numbers); the
  employers, places and schools declared in ``household.yaml`` (``employers`` / ``places`` / ``schools``) become
  ``[employer]`` / ``[place]`` / ``[school]``.
* ``[privacy] model_detail`` (default ``coarse``) generalises the quasi-identifiers for the model: the payer of an
  ``income.salary`` becomes ``[employer]``, the payee of ``kids.school`` (or a declared school) ``[school]``, a merchant
  the classifier's person guard considers a person, or a small unknown business with no organisation word (1-3
  capitalised words, few payments, not a subscription / housing / insurance / tax / debt line) becomes
  ``[merchant:<category>]-<hash>``, and a trailing town (declared in ``places`` or a suffix shared by several merchants) is
  cut from merchant titles. ``standard`` keeps merchant titles except what the person guard flags. In both modes a given
  name masked inside a person-like run masks the WHOLE run (``VELLARD NATHALIE`` -> ``[person]``).
* the message of an anomaly is REBUILT from its structured fields after redaction, never filtered after the fact.
The CLI and the UI keep the raw values: nothing here changes what ``coach ...`` prints.
"""
from __future__ import annotations

import hashlib
import re

from coach.analytics import identity
from coach.analytics.regions import strip_regions
from typing import Callable, Optional


ENUM_KEYS = {"category", "kind", "type", "cadence", "direction", "severity", "status", "currency", "source", "method",
             "effect", "certainty", "amount_mode", "confidence_label", "rule", "pace_basis", "balance_type", "purpose",
             "date", "month", "period", "first", "last", "as_of", "next_expected", "first_date", "last_date", "target_date",
             "projected_date", "start_date", "current_as_of", "min_date", "first_negative", "first_at_risk", "id",
             "series_id", "ref", "member", "members", "among", "person", "payer", "owed_to"}   # E14: member pseudonyms, kept as they are
KEEP_IDS = ("rec_", "anm_", "chg_")
_SHAPE = re.compile(r"^(-?\d+(\.\d+)?|\d{4}-\d{2}(-\d{2})?)$")


VAULT_GENERIC = {"TOP", "EUR", "USD", "GBP", "CHF", "FROM", "VERS", "DEPUIS", "POCKET", "VAULT", "JAR", "COFFRE", "CAGNOTTE", "THE", "ACCOUNT",
                 "COMPTE", "SAVINGS", "EPARGNE"}
PUBLIC_BODY = re.compile(r"^(CAF|CPAM|URSSAF|MSA|CARSAT|DDFIP|DGFIP|SIP|MDPH|POLE EMPLOI|FRANCE TRAVAIL)\b(?=\s+\S|$)")
VAULT = re.compile(r"(?i)^(to|from|vers|depuis)\s+\S|\b(vault|pocket|jar|cagnotte|coffre)\b")


def _cw(w: str) -> str:
    """A word of a merchant title, without punctuation or accents, upper case."""
    return identity.strip_accents(re.sub(r"[^\w]", "", w)).upper()


def stable_hash(key: str) -> str:
    return "h_" + hashlib.sha1(key.encode()).hexdigest()[:10]


BRAND_MIN_TX = 5                      # a merchant paid this often is treated as a business, not a sole trader
OPEN_CATS = ("subscriptions.", "insurance.", "taxes.", "debt.", "fees.", "transport.car_loan", "housing.mortgage", "housing.rent",
             "housing.energy", "housing.water", "housing.property_charges", "housing.home_insurance",
             "housing.rental_property_loan")      # lines that are contracts / utilities: kept by name even if rarely paid
MARKERS = re.compile(r"^(\[(person|family|holder|NAME|name)\]|adult-\d+|kid-\d+|child-\d+)[,.;:]?$")


def collapse_person_runs(text: str) -> str:
    """'VELLARD [person]' -> '[person]': the words around a masked given name are the rest of the person's name."""
    from coach.classify.candidates import LEGAL_FORMS
    from coach.classify.parsers.common import CONNECTORS, ORG_WORDS, strip_accents
    toks = text.split(" ")
    stop = ORG_WORDS | LEGAL_FORMS | CONNECTORS | {"CHEZ", "A", "AU", "PAR", "POUR", "VERS", "FROM", "TO"}

    def namey(t: str) -> bool:
        w = re.sub(r"[^A-Za-z\u00C0-\u017F'’-]", "", t)
        return bool(w) and w == t.strip(",.;:") and w[0].isupper() and strip_accents(w).upper() not in stop and not MARKERS.match(t)

    out, i = [], 0
    while i < len(toks):
        if MARKERS.match(toks[i]):
            while out and namey(out[-1]):
                out.pop()
            j = i + 1
            while j < len(toks) and namey(toks[j]):
                j += 1
            out.append("[person]")
            i = j
        else:
            out.append(toks[i])
            i += 1
    return " ".join(out)


class Redactor:
    def __init__(self, ds, store, con=None, detail: str = "coarse", derived=None):
        from coach.memory.context import Scrubber
        self.detail = detail
        hh0 = store.load_plain("household.yaml") if store.exists("household.yaml") else {}
        self.derived = derived or identity.derive_terms(con, store, ds, hh0)
        self.sc = Scrubber(store, con)
        for k, v in list(self.sc.mapping.items()):
            self.sc.mapping[k] = re.sub(r"^child-", "kid-", v)             # (kept for old mappings: the scrubber now says kid-N itself)
        self.reverse: dict[str, str] = {}
        self.tx_reverse: dict[str, str] = {}
        counters: dict[str, int] = {}
        self.account: dict[str, str] = {}      # uid -> pseudonym
        self.label: dict[str, str] = {}        # label -> pseudonym
        for a in sorted(ds.accounts.values(), key=lambda a: a.uid):
            p = a.purpose or "other"
            counters[p] = counters.get(p, 0) + 1
            ps = f"account-{p}-{counters[p]}"
            self.account[a.uid] = ps
            self.label[a.label] = ps
            self.reverse[ps] = a.label
        self.owner: dict[str, str] = {}
        extra = 0
        for o in sorted({a.owner for a in ds.accounts.values() if a.owner}):
            if o.lower() == "joint":
                self.owner[o] = "joint"
                continue
            ps = self.sc.person(o)
            if not ps or ps.startswith("["):
                extra += 1
                ps = f"person-{extra}"
            ps = re.sub(r"^child-", "kid-", ps)
            self.owner[o] = ps
            self.reverse[ps] = o
        hh = store.load_plain("household.yaml") if store.exists("household.yaml") else {}
        masks = []
        for field_, tag in (("employers", "[employer]"), ("places", "[place]"), ("schools", "[school]")):
            for name in hh.get(field_) or []:
                if isinstance(name, str) and name.strip():
                    masks.append((name.strip(), tag))
        known = {m[0].lower() for m in masks}
        for name in self.derived.employers:                       # found from the salary payers / work-tagged payees
            if name.lower() not in known:
                masks.append((name, "[employer]"))
        for name in self.derived.places:                          # declared + found towns
            if name.lower() not in known:
                masks.append((name, "[place]"))
        by_tag: dict = {}
        for n, t in masks:
            by_tag.setdefault(t, []).append(n)
        # folded matching (case AND accents), the same normaliser as the privacy guard
        self._masks = [(identity.term_regex(by_tag[t]), t) for t in ("[employer]", "[school]", "[place]") if by_tag.get(t)]
        self._masks = [m for m in self._masks if m[0] is not None]
        self.vault_terms: set = set()
        self._build_entities(ds, store, con, hh)
        self._masks += [(identity.term_regex(self.vault_terms), "[vault]")] if self.vault_terms else []
        # final sweep: every term the guard refuses is masked too, so the redactor's output always passes the guard
        self._sweep = identity.term_regex(identity.guard_terms(store, con, self.derived.all_terms()))
        labels = sorted(self.label, key=len, reverse=True)
        self._label_rx = re.compile(r"(?<![\w])(" + "|".join(re.escape(x) for x in labels) + r")(?![\w])", re.I) if labels else None
        uids = [u for u in sorted(self.account, key=len, reverse=True) if len(u) >= 8]     # a short uid is not searched inside text
        self._uid_rx = re.compile(r"(?<![\w-])(" + "|".join(re.escape(x) for x in uids) + r")(?![\w-])") if uids else None

    # -- merchant generalisation
    def _build_entities(self, ds, store, con, hh) -> None:
        from collections import Counter, defaultdict
        from coach.analytics.recurring import norm_key
        from coach.classify.candidates import LEGAL_FORMS, household_names, known_merchants
        from coach.classify.parsers.common import FIRST_NAMES, ORG_WORDS, strip_accents
        from coach.memory.qgen import _person_like
        n_tx: Counter = Counter()
        cats: dict = defaultdict(Counter)
        trailing: dict = defaultdict(set)
        for t in ds.whole:
            name = t.entity or t.mkey
            n_tx[name] += 1
            cats[name][t.category] += 1
            last = [_cw(w) for w in (t.mkey or "").split()[-1:]] or [""]
            if last[0].isalpha() and len(last[0]) >= 4:
                trailing[last[0]].add(name)
        fam, first = household_names(con) if con is not None else (set(), set())
        known = known_merchants(con) if con is not None else set()
        places = {p.strip().upper() for p in (hh.get("places") or []) if isinstance(p, str)}
        derived_places = {strip_accents(w).upper() for p in self.derived.places for w in re.split(r"[\s'-]+", p) if len(w) >= 3}
        cities = {w for w, names in trailing.items() if len(names) >= 3 and w not in ORG_WORDS and w not in LEGAL_FORMS
                  and w not in FIRST_NAMES} | {w for p in places for w in p.split()} | derived_places
        schools = {x.strip().lower() for x in (hh.get("schools") or []) if isinstance(x, str)}
        self.entity_map: dict[str, str] = {}
        for name, n in n_tx.items():
            cat = cats[name].most_common(1)[0][0]
            toks = [w for w in re.split(r"[^A-Za-z'’-]+", strip_accents(name)) if w]
            upper = {w.upper() for w in toks}
            org = bool(upper & (ORG_WORDS | LEGAL_FORMS))
            rep = name
            pub = PUBLIC_BODY.match(strip_accents(name).upper())
            if pub:                                              # "CAF de la Manche" -> "CAF": the department / town is a quasi-identifier
                rep = pub.group(1).title() if len(pub.group(1)) > 4 else pub.group(1)
            elif cat.startswith(("transfer.", "savings.", "income.transfer")) and VAULT.search(name):
                rep = f"[vault]-{stable_hash(name)[2:8]}"        # a vault / pocket / jar name is chosen by the user
                self.vault_terms |= {w.lower() for w in re.split(r"[^A-Za-z]+", strip_accents(name))
                                     if len(w) >= 3 and w.upper() not in VAULT_GENERIC}
            elif strip_regions(name) != name:                    # "NEXITY NORMANDIE" -> "NEXITY"
                rep = strip_regions(name)
            if rep != name:
                pass
            elif self.detail == "coarse":
                if cat == "income.salary":
                    rep = "[employer]"
                elif cat == "kids.school" or name.lower() in schools or any(x in name.lower() for x in schools):
                    rep = "[school]"
            if rep == name:
                person = _person_like(name.upper(), None, set(), fam, first, known, ())
                small = (self.detail == "coarse" and not org and 1 <= len(toks) <= 3 and n < BRAND_MIN_TX
                         and not cat.startswith(OPEN_CATS) and all(w[0].isalpha() for w in toks))
                guard = person and not org or (person and self.detail == "coarse" and n < BRAND_MIN_TX and not cat.startswith(OPEN_CATS))
                if guard or small:
                    rep = f"[merchant:{cat}]-{stable_hash(name)[2:8]}"
            if rep == name and self.detail == "coarse":
                words = name.split()
                while len(words) > 1 and _cw(words[-1]) in cities:           # "SHOP (TOWN)" and "SHOP TOWN" alike
                    words.pop()
                if derived_places:                               # declared / found towns are cut wherever they stand in a title
                    kept = [w for w in words if _cw(w) not in derived_places]
                    words = kept or words
                rep = " ".join(words)
            if rep != name:
                self.entity_map[name] = rep
                self.entity_map.setdefault(norm_key(name), rep)
        names = sorted(self.entity_map, key=len, reverse=True)
        self._entity_rx = re.compile(r"(?<![\w])(" + "|".join(re.escape(x) for x in names) + r")(?![\w])") if names else None

    # -- pieces
    def tx(self, key: str) -> str:
        h = stable_hash(key)
        self.tx_reverse[h] = key
        return h

    def text(self, s: str) -> str:
        if not s:
            return s
        if s in self.entity_map:
            s = self.entity_map[s]
            if s.startswith("["):                      # a placeholder ([merchant:...], [school], [vault]-...) is final
                return s
            # a generalised title (a trailing town / suffix cut) can still hold a declared term: the masks below apply to it too
        elif self._entity_rx:
            s = self._entity_rx.sub(lambda m: self.entity_map[m.group(0)], s)
        for rx, tag in self._masks:
            s = identity.mask_folded(s, rx, tag)
        if self._label_rx:
            s = self._label_rx.sub(lambda m: self.label[next(k for k in self.label if k.lower() == m.group(0).lower())], s)
        if self._uid_rx:
            s = self._uid_rx.sub(lambda m: self.account[m.group(0)], s)
        s = collapse_person_runs(self.sc.scrub(s))
        return identity.mask_folded(s, self._sweep, "[redacted]") if self._sweep else s

    def owner_of(self, o):
        return self.owner.get(o, self.text(o)) if isinstance(o, str) else o

    # -- the walk
    def walk(self, obj, key: Optional[str] = None):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k in ("account", "uid") and isinstance(v, str):
                    out[k] = self.account.get(v, self.tx(v) if k == "uid" else self.text(v))
                elif k == "owner":
                    out[k] = self.owner_of(v)
                elif k == "by_owner" and isinstance(v, dict):            # the KEYS are owner values
                    out[k] = {self.owner_of(kk): self.walk(vv, kk) for kk, vv in v.items()}
                elif k == "owners" and isinstance(v, list):
                    out[k] = [self.owner_of(x) for x in v]
                elif k == "tx_key" and isinstance(v, str):
                    out[k] = self.tx(v)
                elif k == "evidence" and isinstance(v, list):
                    out[k] = [x if not isinstance(x, str) or x.startswith(KEEP_IDS) else self.tx(x) for x in v]
                elif k in ("label", "account_label") and isinstance(v, str):
                    out[k] = self.label.get(v, self.text(v))
                elif k == "accounts" and isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                    out[k] = [self.label.get(x, self.account.get(x, self.text(x))) for x in v]
                else:
                    out[k] = self.walk(v, k)
            if out.get("type") in ANOMALY_TYPES and "message" in out:
                out["message"] = anomaly_message(out)
            return out
        if isinstance(obj, list):
            return [self.walk(x, key) for x in obj]
        if isinstance(obj, str):
            if key in ENUM_KEYS or _SHAPE.match(obj):
                return obj
            return self.text(obj)
        return obj


ANOMALY_TYPES = {"category_spike", "duplicate_charge", "new_merchant", "large_transaction"}


def anomaly_message(a: dict) -> str:
    """The sentence of an anomaly, rebuilt from its (already redacted) structured fields."""
    t, subj, per = a["type"], a.get("subject"), a.get("period")
    amount, base = a.get("amount"), a.get("baseline")
    if t == "category_spike":
        return f"Spending in {subj} in {per} is {amount} EUR, versus a typical {base} EUR per month" + \
            (f" (robust z-score {a['score']})." if a.get("score") is not None else " (usually nothing in this category).")
    if t == "duplicate_charge":
        return f"{len(a.get('evidence') or [])} payments of {base} EUR to {subj} close together (last on {per})."
    if t == "new_merchant":
        return f"New merchant {subj} (first seen {per}) with a payment of {amount} EUR."
    return f"A payment of {amount} EUR in {subj} on {per} is far above the usual {subj} payments (median {base} EUR)."


def redacted_registry(con, cfg, today=None) -> "RedactedRegistry":
    from coach.analytics import api
    from coach.memory.store import MemoryStore
    ds = api.build_dataset(con, cfg, today)
    store = MemoryStore(cfg.memory_dir, history=False)
    red = Redactor(ds, store, con, getattr(cfg, "privacy_model_detail", "coarse"))
    return RedactedRegistry(ds, red, api.registry())


class RedactedRegistry:
    """name -> callable(**params) -> redacted JSON-safe dict/list. `parameters` that name an account or owner are mapped
    back from their pseudonyms (so the model can say 'account-main-1')."""

    def __init__(self, ds, red: Redactor, raw: dict[str, Callable]):
        self.ds, self.red, self._raw = ds, red, raw

    def __contains__(self, name):
        return name in self._raw

    def names(self):
        return sorted(self._raw)

    def call(self, name: str, **params):
        from coach.analytics.common import Scope
        scope = params.pop("scope", None)
        ds = self.ds
        if scope is not None and not isinstance(scope, Scope):
            back = {v: k for k, v in self.red.account.items()}
            obacks = {v: k for k, v in self.red.owner.items()}
            mbacks = {v: k for k, v in self.red.sc.pseudo.items()}
            for ps in scope.get("members") or []:               # E14-4: a person's view of the data (pseudonym -> member id, server side)
                ds = ds.member_view(mbacks.get(ps, ps))
            scope = Scope.make(owners=[obacks.get(o, o) for o in scope.get("owners") or []] or None,
                               purposes=scope.get("purposes"),
                               accounts=[back.get(a, a) for a in scope.get("accounts") or []] or None)
        if scope is not None and name not in ("coverage", "calendar", "goals", "budget_status"):
            params["scope"] = scope
        res = self._raw[name](ds, **params)
        data = res if isinstance(res, (list, dict)) else res.to_dict()
        return self.red.walk(data)

    def __getitem__(self, name: str):
        return lambda **kw: self.call(name, **kw)
