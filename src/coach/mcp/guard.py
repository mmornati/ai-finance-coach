"""The last line of defence of the finance tools (E6-1 / E6-8). Three independent mechanisms, none of which trusts the
code that produced the data:

* :class:`PrivacyGuard` - a FINAL assertion on every tool output: no household member name (or alias, or holder spelling),
  no declared employer / place / school, no real account label or uid, no IBAN / e-mail / filesystem path / key-like
  string. It FAILS CLOSED: a hit withholds the whole output (and never echoes what it found).
* :func:`wrap_untrusted` / :func:`scan_injection` - merchant and description text is written by third parties: it is
  truncated, stripped of control characters and wrapped as ``{"untrusted_text": ...}``, and instruction-like text in it
  marks the session ``suspicious``.
* :class:`NumberLedger` - the principle "numbers come from code": every number the model writes can be checked against the
  numbers the tools returned in the same session.
"""
from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from coach.memory.context import STOP
from coach.memory.docverify import DOC_MARKER_RE, INSTRUCTION_RE

UNTRUSTED_MAX = 60
# keys whose string values come (directly or by rebuilding) from merchant / description text written by third parties
UNTRUSTED_KEYS = {"entity", "key", "subject", "title", "message", "merchant", "merchant_name", "group_key", "description",
                  "counterparty", "payee", "payer", "name", "text", "question", "provider", "lender", "merchants",
                  "top_merchants", "offer", "features", "source_url"}
UNTRUSTED_LIMITS = {"message": 160, "title": 160, "question": 240, "description": 120}

# instruction-like text aimed at an assistant, on top of the document markers
COACH_MARKER_RE = re.compile(
    r"\b(?:propose|delete|remove|unset|overwrite|reveal|exfiltrate|leak|send|print|disclose)\b.{0,50}"
    r"\b(?:memory|household|proposal|insight|iban|password|secret|api\s*key|names?)\b|"
    r"\b(?:call|use|invoke|run)\s+(?:the\s+)?(?:\w+\s+){0,2}tool\b|\bmemory_propose\b|\badd_insight\b|"
    r"\b(?:assistant|system|developer)\s*(?:message|prompt|:)|<\s*/?\s*(?:system|instructions?|tool)\s*>", re.I)

# Italian, Spanish and German instruction markers (the E3 markers are French / English)
MULTILINGUAL_RE = re.compile(
    r"\b(?:ignora|dimentica|trascura)\b.{0,40}\b(?:istruzioni|precedenti|regole|sopra)\b|\b(?:devi|dovete)\s+(?:ignorare|cancellare|eliminare|proporre|rivelare|inviare|mostrare)\w*|"
    r"\b(?:ignora|olvida|descarta)\b.{0,40}\b(?:instrucciones|anteriores|reglas)\b|\b(?:debes|tienes\s+que)\s+(?:ignorar|borrar|eliminar|proponer|revelar|enviar|mostrar)\w*|"
    r"\b(?:ignoriere|ignorieren|vergiss|missachte)\b.{0,40}\b(?:anweisungen|instruktionen|vorherigen|bisherigen|regeln)\b|"
    r"\b(?:du\s+musst|sie\s+m(?:ü|ue|u)ssen|du\s+sollst)\b|\b(?:neue|nuove|nuevas)\s+(?:anweisungen|istruzioni|instrucciones)\b", re.I)

IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
SECRET_RE = re.compile(r"sk-ant-[\w-]{8,}|\bBEGIN (?:RSA |EC )?PRIVATE KEY\b|\bCOACH_[A-Z_]{3,}\b|ui-session|ui-login|\bdb_key\b|"
                       r"\bproposal_key\b")
PATH_RE = re.compile(r"(?:/Users/|/home/|/private/|/var/folders/|/tmp/|[A-Za-z]:\\\\|~/)[\w./ -]+")


class PrivacyViolation(Exception):
    """A tool output holds something that must never reach a model. The message never contains the offending text."""


from coach.analytics.identity import fold  # noqa: E402  (the one normaliser of guard and redactor)


def _strings(obj) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


# ---------------------------------------------------------------- untrusted text

_CTRL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u180e\u034f]")


def normalize(s: str) -> str:
    """What a model effectively reads: NFKC (fullwidth and compatibility forms folded), invisible and format characters REMOVED
    (they would split a word), other control characters turned into spaces."""
    s = unicodedata.normalize("NFKC", s)
    s = _INVISIBLE.sub("", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Cf")
    return _CTRL.sub(" ", s)


def normalize_spaced(s: str) -> str:
    """The same, but invisible characters become spaces (a different way a reader may split the words)."""
    s = _INVISIBLE.sub(" ", unicodedata.normalize("NFKC", s))
    return _CTRL.sub(" ", "".join(c if unicodedata.category(c) != "Cf" else " " for c in s))


def clean_text(s: str, limit: int = UNTRUSTED_MAX) -> str:
    s = normalize(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"



def scan_injection(text: str) -> list[str]:
    """Instruction-like passages in a piece of data (reuses the document markers of E3-5)."""
    if not isinstance(text, str):
        return []
    if len(text) < 8:
        return []
    hits = []
    for variant in dict.fromkeys((normalize(text), normalize_spaced(text))):
        for rx in (DOC_MARKER_RE, INSTRUCTION_RE, COACH_MARKER_RE, MULTILINGUAL_RE):
            for m in rx.finditer(variant):
                if m.group(0).strip().lower() == "llm":
                    continue                   # the bare word is a marker for documents; here it is just a word (a label's source)
                hits.append(clean_text(variant[max(0, m.start() - 10):m.end() + 20], 60))
                break
        if hits:
            break
    return hits[:1]


# keys that carry text written by this code (static hints, rule descriptions, assumptions): never scanned for injection
TRUSTED_TEXT_KEYS = {"note", "notes", "hint", "next", "how_to_change", "meaning", "rule", "assumptions", "flags", "warnings", "error", "security_notice",
                     # text of the E7 skills' tools that this code writes itself (rules, disclaimers, commands, checklists)
                     "how", "do", "command", "disclaimer", "conditions", "method", "summary", "law", "ceilings", "documents_to_keep",
                     "missing_info", "missing", "what_i_need", "unknown", "penalty_basis", "heading", "rate", "source", "reason_code"}


def scan_all(obj, key: Optional[str] = None) -> list[str]:
    """Injection markers in the free text of a structure (text this code wrote itself is skipped)."""
    seen: list[str] = []
    if isinstance(obj, str):
        return [] if key in TRUSTED_TEXT_KEYS else scan_injection(obj)
    if isinstance(obj, dict):
        for k, v in obj.items():
            seen += scan_all(v, k)
            if len(seen) >= 3:
                break
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            seen += scan_all(v, key)
            if len(seen) >= 3:
                break
    return seen[:3]


def wrap_untrusted(obj, key: Optional[str] = None, limit: int = UNTRUSTED_MAX):
    """Return `obj` with every free-text value (see UNTRUSTED_KEYS) replaced by ``{"untrusted_text": <cleaned>}``."""
    if isinstance(obj, dict):
        if set(obj) == {"untrusted_text"}:
            return obj
        return {k: wrap_untrusted(v, k, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [wrap_untrusted(v, key, limit) for v in obj]
    if isinstance(obj, str) and key in UNTRUSTED_KEYS:
        return {"untrusted_text": clean_text(obj, max(limit, UNTRUSTED_LIMITS.get(key, 0)))}
    return obj


# ---------------------------------------------------------------- the privacy assertion

class PrivacyGuard:
    """Built from the household memory and the database at the start of a session (not from the redactor: it is the
    independent check on it)."""

    def __init__(self, store, con, cfg, extra_terms: Iterable[str] = ()):
        from coach.analytics.identity import guard_terms
        terms = guard_terms(store, con, extra_terms)
        pseudo: set[str] = set()
        # a term inside a pseudonym such as "account-main-1" cannot be searched for as a word on its own
        self.terms = sorted(terms - pseudo, key=len, reverse=True)
        self._rx = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in self.terms) + r")(?![a-z0-9])") if self.terms else None
        roots = {str(getattr(cfg, a, "") or "") for a in ("root", "data_dir", "memory_dir", "db_path")}
        self._paths = sorted((p for p in roots if len(p) > 3), key=len, reverse=True)

    def violations(self, obj) -> list[str]:
        """Categories of what was found (never the text itself)."""
        found: list[str] = []
        text = json.dumps(obj, ensure_ascii=False, default=str)
        low = fold(text)
        if self._rx and self._rx.search(low):
            found.append("household name, employer, place, school or account label")
        compact = text.replace(" ", "")
        if IBAN_RE.search(text) or re.search(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", compact):
            found.append("IBAN-like value")
        if EMAIL_RE.search(text):
            found.append("e-mail address")
        if SECRET_RE.search(text):
            found.append("secret-like string")
        if PATH_RE.search(text) or any(p in text for p in self._paths):
            found.append("filesystem path")
        return found

    def mask(self, obj):
        """The structure with every hit replaced by ``[redacted]`` (and True when something was). Used where refusing would be an
        oracle (add_insight): the model learns nothing from the outcome."""
        hit = [False]

        def one(t: str) -> str:
            low = fold(t)
            out = t
            if self._rx:
                # positions in the folded text equal positions in t only when folding keeps lengths: work word by word
                words = re.split(r"(\W+)", t)
                for i, w in enumerate(words):
                    if w and self._rx.search(fold(w)):
                        words[i] = "[redacted]"
                        hit[0] = True
                out = "".join(words)
                for term in self.terms:                       # multi-word terms spanning several words
                    if " " in term and term in fold(out):
                        out = re.sub(re.escape(term), "[redacted]", out, flags=re.I)
                        hit[0] = True
            for rx in (IBAN_RE, EMAIL_RE, SECRET_RE, PATH_RE):
                if rx.search(out):
                    out = rx.sub("[redacted]", out)
                    hit[0] = True
            for p in self._paths:
                if p in out:
                    out = out.replace(p, "[redacted]")
                    hit[0] = True
            return out

        def walk(o):
            if isinstance(o, str):
                return one(o)
            if isinstance(o, dict):
                return {k: walk(v) for k, v in o.items()}
            if isinstance(o, list):
                return [walk(v) for v in o]
            return o
        res = walk(obj)
        return res, hit[0]

    def assert_clean(self, obj) -> None:
        v = self.violations(obj)
        if v:
            raise PrivacyViolation("output withheld by the privacy filter (" + "; ".join(v) + ")")


# ---------------------------------------------------------------- numbers come from code

_DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_REF_RE = re.compile(r"\b(?:h_[0-9a-f]{10}|(?:rec|anm|chg|cin|ins)_[0-9a-f]{6,}|p-\d{8}-[0-9a-f]{6})\b")
_NUM_RE = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:[\u202f\u00a0 ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)*)")


def _norm(tok: str) -> Optional[Decimal]:
    t = re.sub(r"[\u202f\u00a0 ]", "", tok)
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in t:
        t = t.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", t) else t.replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        return abs(Decimal(t))
    except InvalidOperation:
        return None


def numbers_in_text(text: str, *, strict: bool = True) -> list[Decimal]:
    """Numbers written in a text (French and English notation); dates, refs and ids do not count. Years never count; not
    strict: the integers 0-31 (days, months, counts in prose) are skipped too."""
    if not isinstance(text, str):
        return []
    t = _DATE_RE.sub(" ", _REF_RE.sub(" ", text))
    out = []
    for m in _NUM_RE.finditer(t):
        raw = m.group(0)
        d = _norm(raw)
        if d is None:
            continue
        whole = d == d.to_integral_value()
        if whole and re.fullmatch(r"(?:19|20)\d\d", raw):
            continue                                                   # a year
        if not strict and whole and d <= 31:
            continue
        out.append(d)
    return out


def _walk_numbers(obj, acc: list[Decimal]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        try:
            acc.append(abs(Decimal(str(obj))))
        except InvalidOperation:
            pass
    elif isinstance(obj, str):
        acc.extend(numbers_in_text(obj))
        d = _norm(obj.strip()) if re.fullmatch(r"-?\d+(?:\.\d+)?", obj.strip()) else None
        if d is not None:
            acc.append(d)
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_numbers(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_numbers(v, acc)


def _canon(d: Decimal) -> str:
    return format(d.normalize(), "f") if d != 0 else "0"


class NumberLedger:
    """The numbers the tools returned in one session. ``unverified(text_or_obj)`` lists the numbers of a model-written text
    (or of structured findings) that no tool result contains, as written (rounding to the unit or to one decimal is
    tolerated: the model may say 1,235 for 1234.56)."""

    def __init__(self):
        self.known: set[str] = set()

    def add(self, obj) -> None:
        acc: list[Decimal] = []
        _walk_numbers(obj, acc)
        for d in acc:
            for q in (d, d.quantize(Decimal("1")), d.quantize(Decimal("0.1")), d.quantize(Decimal("0.01"))):
                self.known.add(_canon(q))

    def unverified(self, obj, *, strict: bool = False) -> list[str]:
        out: list[str] = []
        texts: list[Any] = []

        def collect(o):
            if isinstance(o, bool) or o is None:
                return
            if isinstance(o, (int, float)):
                texts.append(("num", o))
            elif isinstance(o, str):
                texts.append(("str", o))
            elif isinstance(o, dict):
                for v in o.values():
                    collect(v)
            elif isinstance(o, (list, tuple)):
                for v in o:
                    collect(v)
        collect(obj)
        for kind, v in texts:
            if kind == "num":
                if strict and isinstance(v, int) and 1900 <= v <= 2100:
                    continue
                if not strict and ((float(v) == int(v) and 0 <= abs(v) <= 31) or (isinstance(v, int) and 1900 <= v <= 2100)):
                    continue
                nums = [abs(Decimal(str(v)))]
            else:
                nums = numbers_in_text(v, strict=strict)
            for d in nums:
                if _canon(d) not in self.known and _canon(d) not in out:
                    out.append(_canon(d))
        return out[:30]
