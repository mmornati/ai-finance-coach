"""Redaction applied to EVERY item before it is sent to an LLM (E2-6, E11-1): IBANs, e-mail addresses, phone
numbers, long digit runs / reference numbers, and the household's own name tokens. Person-to-person transfers are
additionally never turned into items at all (see :mod:`coach.classify.candidates`)."""
from __future__ import annotations

import re

IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{3,5}){3,8}\b", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<![\w])(?:\+|00)\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}(?![\w])|"
                      r"(?<![\w])0\d(?:[\s.-]?\d{2}){4}(?![\w])")
LONG_DIGITS_RE = re.compile(r"\d{6,}")
HEXID_RE = re.compile(r"\b(?=[0-9a-f-]*\d)(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,})\b",
                      re.I)

# a title followed by a name ("DR DUPONT MARIE", "MME X"): the title stays, the name goes (P5)
TITLED_RE = re.compile(r"\b((?:DR|DOCTEUR|DOCTEURE|DOTT|DOTTORE|MAITRE|PROF|PROFESSEUR|MME|MR|MLLE|MONSIEUR|MADAME)\b\.?)"
                       r"\s+(?!\[)[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*)?", re.I)

STRING_FIELDS = ("key", "raw_example", "name", "descriptor")


def redact(text: str | None, names: set[str] | frozenset[str] = frozenset()) -> str | None:
    if not text:
        return text
    t = EMAIL_RE.sub("[EMAIL]", text)
    t = IBAN_RE.sub("[IBAN]", t)
    t = HEXID_RE.sub("[ID]", t)
    t = PHONE_RE.sub("[PHONE]", t)
    t = LONG_DIGITS_RE.sub("[NUM]", t)
    t = TITLED_RE.sub(lambda m: f"{m.group(1)} [NAME]", t)
    if names:
        pat = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b", re.I)
        t = pat.sub("[NAME]", t)
    return t


def redact_item(item: dict, names: set[str] | frozenset[str] = frozenset()) -> dict:
    """Copy of a prompt item with every free-text field redacted (also inside nested `similar` examples)."""
    out = dict(item)
    for f in STRING_FIELDS:
        if isinstance(out.get(f), str):
            out[f] = redact(out[f], names)
    if isinstance(out.get("similar"), list):
        out["similar"] = [redact_item(x, names) if isinstance(x, dict) else x for x in out["similar"]]
    return out
