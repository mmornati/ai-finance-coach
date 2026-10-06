"""Which merchant keys may be sent to an LLM, and which are held back (E2-4, E2-6, E11-1).

Privacy rule: a name of a natural person never leaves the machine. The guard therefore works by DEFAULT-DENY for
everything that is not a shop: for transfer-like items (transfer_in/out, 'other', direct debits) a key is sent only
when it shows it is an organisation (legal form or organisation word, a single brand-like word) or is already a
known merchant (labelled, and seen on a card payment / direct debit), or matches the configurable allowlist
(`[classify] llm_allowlist`). Everything else is held back and shown in `coach classify review` as
`held_back_person_like`. Card items are shops; they are only checked for titled people (DR X, MME X).

The same eligibility test decides which labelled merchants may serve as few-shot examples or kNN hints: only keys
seen exclusively as card / card_refund / direct_debit, labelled outside transfer.* / income.*, and passing the guard.
"""
from __future__ import annotations

import re

from coach.classify.parsers.common import (
    CONNECTORS, FIRST_NAMES, ORG_WORDS, PROF_TITLE_RE, P2P_PREFIXES, looks_like_person, strip_accents)
from coach.classify.rules import needs_llm_sql, rule_category

CARDISH = {"card", "card_refund", "direct_debit"}
# items whose counterparty may be a person: held back unless shown to be an organisation
HOLD_TYPES = {"transfer_in", "transfer_out", "other", "direct_debit"}
LEGAL_FORMS = {"SAS", "SARL", "SA", "SNC", "EURL", "SCI", "SASU", "SRL", "SPA", "GMBH", "LTD", "INC", "LLC", "BV",
               "NV", "AG", "PLC", "SCOP", "SCM", "SELARL", "SEL", "GIE", "OY", "AB"}
TITLE_ANY_RE = re.compile(r"^(?:DR|DOCTEUR|DOCTEURE|DOTT|DOTTORE|MAITRE|PROF|PROFESSEUR|MME|MR|MLLE|MONSIEUR|MADAME|SIG|SIGNORA)\b",
                          re.I)


# keys under an entity whose category the USER set: that category decides, nothing to ask the model or the user
USER_ENTITY_KEYS_SQL = ("(SELECT a3.merchant_key FROM merchant_aliases a3 JOIN merchant_entities en3 ON en3.id=a3.entity_id "
                        "WHERE en3.category_user=1 AND en3.category IS NOT NULL)")


def household_names(con) -> tuple[set[str], set[str]]:
    from coach.classify.parsers.common import household
    h = household(con)
    return h.family, h.first_names


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^A-Z0-9']+", strip_accents(text or "").upper()) if t]


def organisation_like(text: str, family: set[str], first: set[str]) -> bool:
    toks = [t for t in _tokens(text) if not t.isdigit()]
    if not toks:
        return False
    if any(t in ORG_WORDS or t in LEGAL_FORMS for t in toks):
        return True
    # a single brand-like word ("ZENOVIA", "UNICEF"): not a given name or the household's own name
    if len(toks) == 1:
        t = toks[0]
        return len(t) >= 4 and t not in FIRST_NAMES and t not in first and t not in family and t.isalpha()
    return False


TOWN_LINKS = {"LEZ", "LES", "LA", "LE", "SUR", "SOUS", "EN", "AUX", "DE", "DU", "DES", "SAINT", "SAINTE", "ST", "STE", "LAS", "LOS", "DEL"}


def prompt_leaks(text: str, places: list[tuple], names: set[str]) -> list[str]:
    """Household names / places found in a STATIC prompt text (instructions, examples): there must be none, whatever the source of an
    example. Used by `enrich` before it sends and by the tests."""
    toks = set(_tokens(text))
    found = {t for p in places for t in p if len(t) >= 4 and t in toks}
    return sorted(found | {n for n in names if len(n) >= 4 and n in toks})


def known_places(con, memory_dir=None, min_keys: int = 3) -> list[tuple]:
    """The towns a descriptor may end with, as token tuples: the declared places (household.yaml, profile.md), the ones derived from
    the identity layer, and every last word shared by `min_keys` different merchant keys (a town suffix: 'X NANTES', 'Y NANTES', 'Z NANTES')."""
    places: list[tuple] = []
    if memory_dir is not None:
        try:
            from coach.analytics.identity import derive_terms
            from coach.memory.store import MemoryStore
            for p in derive_terms(con, MemoryStore(memory_dir, history=False)).places:
                toks = tuple(t for t in _tokens(p))
                if toks:
                    places.append(toks)
        except Exception:                                                          # noqa: BLE001
            pass
    last: dict[str, set] = {}
    for (k,) in con.execute("SELECT DISTINCT merchant_key FROM tx_enriched WHERE merchant_key <> ''"):
        t = _tokens(k)
        if len(t) >= 2 and t[-1].isalpha() and len(t[-1]) >= 4 and t[-1] not in ORG_WORDS and t[-1] not in LEGAL_FORMS:
            last.setdefault(t[-1], set()).add(k)
    places += [(w,) for w, ks in last.items() if len(ks) >= min_keys]
    return sorted(set(places), key=lambda p: -len(p))


def strip_places(text: str, places: list[tuple]) -> str:
    """The descriptor without its town: known place token sequences are removed (longest first, anywhere), then dangling town links
    (LEZ / SUR / SAINT ...) at the end. Never returns a place in a web-search query."""
    toks = _tokens(text)
    changed = True
    while changed and toks:
        changed = False
        for p in places:
            n = len(p)
            for i in range(len(toks) - n + 1):
                if tuple(toks[i:i + n]) == p:
                    toks = toks[:i] + toks[i + n:]
                    changed = True
                    break
            if changed:
                break
    for p in places:                                    # a truncated descriptor ends with the START of a town ('ROQUEMONT LEZ', 'ROQUEMO')
        for k in range(len(p), 0, -1):
            tail = toks[-k:]
            if len(tail) == k and tail[:-1] == list(p[:k - 1]) and (tail[-1] == p[k - 1] if k == len(p) else p[k - 1].startswith(tail[-1]) and len(tail[-1]) >= 4 or tail[-1] == p[k - 1]):
                if k < len(p) or tail == list(p):
                    toks = toks[:-k]
                    break
    while toks and toks[-1] in TOWN_LINKS:
        toks.pop()
    return " ".join(toks)


# brand words that are also common surnames: they do not make a descriptor a business when a given name stands next to them ('LEROY SOPHIE')
SURNAME_LIKE_BRANDS = {"LEROY", "MERLIN", "LECLERC", "FREE", "ORANGE", "CAF", "MAISON"}


def name_adjacent_to_first_name(tokens: list[str], first_names: set[str]) -> bool:
    """A given name next to another name-like word, whatever the length of the descriptor ('VELLARD NATHALIE LYS', 'LEROY SOPHIE'). Words that
    are organisation words, legal forms, connectors or digits are not name-like; a descriptor holding an organisation word is a business."""
    if any((t in ORG_WORDS and t not in SURNAME_LIKE_BRANDS) or t in LEGAL_FORMS for t in tokens):
        return False
    names = FIRST_NAMES | first_names
    word = [t for t in tokens]
    for i, t in enumerate(word):
        if t.split("-")[0] in names or len(t) == 1:
            for j in (i - 1, i + 1):
                if 0 <= j < len(word) and word[j] not in CONNECTORS and word[j].isalpha() and word[j] != t:
                    return True
    return False


def hold_back(key: str, raw: str | None, types: set[str], family: set[str], first: set[str],
              known: set[str] = frozenset(), allow: tuple = (), strict_shop: bool = False, places: list | None = None) -> bool:
    """True when the item must not be sent to an LLM. `strict_shop`: a card key that looks like a person's name is
    refused even if it carries a label (used for examples / hints). `places`: towns to strip before the person check (known_places)."""
    text = raw or key
    toks = set(_tokens(key)) | set(_tokens(text))
    up = strip_accents(key or "").upper()
    if toks & P2P_PREFIXES and (types & (HOLD_TYPES | {"person_transfer_in", "person_transfer_out"})):
        return True
    if toks & family and not types <= {"card", "card_refund"}:
        return True
    if not types & HOLD_TYPES:
        # shops: a titled person (DR X, MME X), or a key that reads like a person's name ("LUCA PARIS": two or more
        # words, a given name or an initial, no organisation word) unless it already carries a label / is allowed
        if TITLE_ANY_RE.match(up) or PROF_TITLE_RE.match(up + " "):
            return True
        if (key in known and not strict_shop) or any(re.search(a, key, re.I) for a in allow):
            return False
        bare = strip_places(key, places or [])
        return (looks_like_person(key, first) or looks_like_person(bare, first)
                or name_adjacent_to_first_name(_tokens(bare) or _tokens(key), first))
    if TITLE_ANY_RE.match(up) or PROF_TITLE_RE.match(up + " "):
        return True
    if key in known or any(re.search(a, key, re.I) for a in allow):
        return False
    return not (organisation_like(key, family, first) or organisation_like(text, family, first))


def known_merchants(con) -> set[str]:
    """Labelled keys that were seen on card payments / direct debits: shops and creditors, not people."""
    return {r[0] for r in con.execute(
        """SELECT DISTINCT m.merchant_key FROM merchants m JOIN tx_enriched e ON e.merchant_key=m.merchant_key
           WHERE e.tx_type IN ('card','card_refund','direct_debit') AND m.category IS NOT NULL""")}


def memory_decided_keys(con, cfg_or_memory_dir) -> set[str]:
    """Merchant keys ALL of whose transactions are decided by a memory annotation. A key with some transactions
    outside the annotation (same shop, other dates / amounts) still needs its label."""
    from coach.classify.rules import categorised
    mem = getattr(cfg_or_memory_dir, "memory_dir", cfg_or_memory_dir)
    total: dict[str, int] = {}
    decided: dict[str, int] = {}
    for t in categorised(con, memory_dir=mem, use_splits=False):
        total[t["key"]] = total.get(t["key"], 0) + 1
        if t["source"] == "memory":
            decided[t["key"]] = decided.get(t["key"], 0) + 1
    return {k for k, n in decided.items() if n == total[k]}


def llm_candidates(con, refresh: bool = False, include_ruled: bool = False, memory_keys: set[str] | None = None,
                   allow: tuple = (), memory_dir=None):
    """-> (candidates, withheld): lists of dicts {key, n, total, types, banks, raw_example, status}."""
    family, first = household_names(con)
    places = known_places(con, memory_dir)
    known = known_merchants(con)
    q = f"""SELECT e.merchant_key, COUNT(*) n, ROUND(SUM(ABS(t.amount)),2), GROUP_CONCAT(DISTINCT e.tx_type),
                   GROUP_CONCAT(DISTINCT COALESCE(a.bank, '')), MAX(e.merchant_raw), MAX(m.source), MAX(m.category)
            FROM tx_enriched e JOIN transactions t USING(tx_key) LEFT JOIN accounts a ON a.uid=t.account_uid
            LEFT JOIN merchants m ON m.merchant_key=e.merchant_key
            WHERE {needs_llm_sql()} AND e.merchant_key <> ''
              AND t.tx_key NOT IN (SELECT out_tx_key FROM transfer_links)
              AND t.tx_key NOT IN (SELECT in_tx_key FROM transfer_links)
              AND ((m.merchant_key IS NULL AND e.merchant_key NOT IN
                        (SELECT a2.merchant_key FROM merchant_aliases a2 JOIN merchant_entities en ON en.id=a2.entity_id
                         WHERE en.category IS NOT NULL))
                   {"OR (m.source IN ('llm','knn') AND e.merchant_key NOT IN " + USER_ENTITY_KEYS_SQL + ")" if refresh else ""})
            GROUP BY 1 ORDER BY 3 DESC"""
    out, withheld = [], []
    for key, n, total, types, banks, raw, _src, _cat in con.execute(q):
        if not include_ruled and rule_category(key):
            continue
        if memory_keys and key in memory_keys:
            continue
        item = dict(key=key, n=n, total=total, types=set((types or "").split(",")),
                    banks=sorted(b for b in set((banks or "").split(",")) if b), raw_example=raw)
        if hold_back(key, raw, item["types"], family, first, known, allow, places=places):
            withheld.append({**item, "status": "withheld_person_like"})
        else:
            out.append({**item, "status": "candidate"})
    return out, withheld


def eligible_label_keys(con, allow: tuple = ()) -> set[str]:
    """Labelled merchants that may be shown to an LLM as example / hint (and form the kNN corpus)."""
    family, first = household_names(con)
    rows = con.execute("""
        SELECT m.merchant_key, MAX(e.merchant_raw), GROUP_CONCAT(DISTINCT e.tx_type)
        FROM merchants m JOIN tx_enriched e ON e.merchant_key=m.merchant_key
        WHERE m.category IS NOT NULL AND m.category <> 'other.uncategorized'
          AND m.category NOT LIKE 'transfer.%' AND m.category NOT LIKE 'income.%'
        GROUP BY m.merchant_key""").fetchall()
    out = set()
    for key, raw, types in rows:
        ts = set((types or "").split(","))
        if not ts <= CARDISH:
            continue
        if hold_back(key, raw, ts, family, first, {key} if "card" in ts or "card_refund" in ts else frozenset(), allow,
                     strict_shop=True):
            continue
        out.add(key)
    return out


def enrich_candidates(con, keys: list[str], allow: tuple = (), memory_dir=None) -> tuple[list[str], list[str]]:
    """(searchable, withheld) for the web enrichment (E11-1): a merchant key may be put in a web search only when it is a SHOP.
    Everything that could be a person is withheld: titled people, person-like names (also with a multi-word town after them), a given
    name next to another name-like word, keys seen on transfers / direct debits, the household's own names. Stricter than the classifier
    (`strict_shop`: a label does not clear a person-like key)."""
    family, first = household_names(con)
    places = known_places(con, memory_dir)
    ok, held = [], []
    for k in keys:
        row = con.execute("SELECT MAX(merchant_raw), GROUP_CONCAT(DISTINCT tx_type) FROM tx_enriched WHERE merchant_key=?", (k,)).fetchone()
        types = set((row[1] or "").split(",")) - {""}
        if (types and not types <= {"card", "card_refund"}) or not strip_places(k, places) \
                or hold_back(k, row[0], types or {"card"}, family, first, frozenset(), allow, strict_shop=True, places=places):
            held.append(k)
        else:
            ok.append(k)
    return ok, held


def search_descriptor(key: str, places: list[tuple]) -> str:
    """What goes into a web-search request for a merchant key: the descriptor with its town removed (the prompt says to search it with
    'France')."""
    return strip_places(key, places) or key
