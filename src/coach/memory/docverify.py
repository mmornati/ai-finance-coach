"""Does a snippet of the document really state the extracted value? (E3-5)

The LLM's answer is untrusted: a field is kept only when the snippet is evidence in the right sense:
  * numbers are read in French and English notation and must carry the right unit/context (EUR for amounts, % for rates,
    km, months, days...). A number that belongs to a DATE is not a number;
  * dates are read as ISO, dd/mm/yyyy, dd.mm.yy, '1er fév. 2023', '5 February 2020';
  * strings and enum values must appear as whole words (no substring luck, minimum length);
  * the sentence of the snippet and its neighbours must not read like an instruction (prompt injection)."""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from typing import Optional

MIN_SNIPPET = 12
MIN_STRING = 3
INSTRUCTION_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override)\b.{0,40}\b(?:previous|above|prior|instructions?|rules?)\b|"
    r"\b(?:system\s+prompt|you\s+(?:must|are|should|will)|as\s+an?\s+(?:ai|assistant|language\s+model)|new\s+instructions?|"
    r"assistant\s*:|set\s+(?:the\s+)?\w+(?:\s+\w+)?\s+to\b|please\s+(?:set|change|update|add|write)|"
    r"(?:ignore|oublie[zs]?|ignorez)\b.{0,30}\b(?:instructions?|consignes?|pr[ée]c[ée]dent\w*)|"
    r"(?:tu\s+dois|vous\s+devez|veuillez\s+(?:mettre|modifier|d[ée]finir)))\b", re.I)

_MONTHS = {
    "janvier": 1, "janv": 1, "january": 1, "jan": 1, "février": 2, "fevrier": 2, "févr": 2, "fevr": 2, "fév": 2, "fev": 2,
    "february": 2, "feb": 2, "mars": 3, "march": 3, "mar": 3, "avril": 4, "avr": 4, "april": 4, "apr": 4, "mai": 5, "may": 5,
    "juin": 6, "june": 6, "jun": 6, "juillet": 7, "juil": 7, "july": 7, "jul": 7, "août": 8, "aout": 8, "august": 8, "aug": 8,
    "septembre": 9, "sept": 9, "september": 9, "sep": 9, "octobre": 10, "oct": 10, "october": 10, "novembre": 11, "nov": 11,
    "november": 11, "décembre": 12, "decembre": 12, "déc": 12, "dec": 12, "december": 12}
_MONTH_RE = "|".join(sorted(map(re.escape, _MONTHS), key=len, reverse=True))
DATE_TOKEN_RE = re.compile(
    rf"(?P<iso>\d{{4}}-\d{{2}}-\d{{2}})|(?P<dmy>\d{{1,2}}[/.\-]\d{{1,2}}[/.\-]\d{{2,4}})|"
    rf"(?P<txt>\d{{1,2}}(?:er)?\s*(?P<mon>{_MONTH_RE})\.?\s+(?P<y>\d{{2,4}}))", re.I)

SYNONYMS = {"fixed": ("fixe", "fixed"), "variable": ("variable", "révisable", "revisable"), "mixed": ("mixte", "mixed"),
            "monthly": ("mensuel", "mensuelle", "monthly", "par mois", "per month", "mois"),
            "yearly": ("annuel", "annuelle", "yearly", "annual", "par an"), "quarterly": ("trimestriel", "trimestrielle", "quarterly"),
            "bimonthly": ("bimestriel", "bimonthly"), "mortgage": ("immobilier", "mortgage", "hypothécaire", "hypothecaire"),
            "loa": ("loa", "option d'achat", "option d’achat"), "lld": ("lld", "longue durée", "longue duree"),
            "car_loan": ("auto", "véhicule", "vehicule", "car loan"), "consumer_loan": ("consommation", "personnel", "consumer"),
            "bnpl": ("bnpl", "paiement en plusieurs fois"),
            "energy": ("électricité", "electricite", "gaz", "energy", "énergie", "energie"),
            "telecom": ("mobile", "internet", "fibre", "telecom"), "streaming": ("streaming", "vidéo", "video"),
            "insurance_home": ("habitation", "home insurance"), "insurance_car": ("auto", "automobile", "car insurance"),
            "insurance_health": ("santé", "sante", "health"), "health": ("santé", "sante", "health"),
            "software": ("logiciel", "software"), "other": (),
            "partial": ("partielle", "partial", "intérêts seuls", "interets seuls"), "total": ("totale", "total"),
            "initial": ("capital initial", "initial"), "outstanding": ("capital restant dû", "capital restant du", "outstanding",
                                                                       "capital restant")}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", s.casefold())
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip()


def _year(y: str) -> int:
    n = int(y)
    return n if len(y) == 4 else 2000 + n if n < 70 else 1900 + n


def dates_in(text: str) -> set:
    found = set()
    for m in DATE_TOKEN_RE.finditer(text):
        try:
            if m.group("iso"):
                y, mo, d = (int(x) for x in m.group("iso").split("-"))
            elif m.group("dmy"):
                d, mo, y = re.split(r"[/.\-]", m.group("dmy"))
                d, mo, y = int(d), int(mo), _year(y)
            else:
                d = int(re.match(r"\d{1,2}", m.group("txt")).group(0))
                mo, y = _MONTHS[m.group("mon").lower()], _year(m.group("y"))
            _dt.date(y, mo, d)
            found.add((y, mo, d))
        except (ValueError, KeyError):
            continue
    return found


UNIT_RE = {
    "eur": r"(?:€|eur\b|euros?\b)", "pct": r"(?:%|pour\s+cent\b|percent\b)", "km": r"(?:km\b|kms\b|kilom[èe]tres?\b)",
    "months": r"(?:mois\b|months?\b|mensualit[ée]s?\b|échéances?\b|echeances?\b|installments?\b|ans?\b|years?\b)",
    "days": r"(?:jours?\b|days?\b)"}
# a number token: grouped thousands ("1 234 567", "250.000"), or a plain decimal
NUM_RE = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)(?![\w]*\d)")


def _candidates(tok: str, unit: str) -> list[float]:
    t = re.sub(r"[   ]", "", tok)
    out = []
    if unit == "pct":                            # rates: a separator is always a decimal point ("3,450 %")
        t = t.replace(",", ".")
        out.append(t)
    else:
        if "," in t and "." in t:
            dec = "," if t.rfind(",") > t.rfind(".") else "."
            out.append(re.sub(r"[.,]", "", t.rsplit(dec, 1)[0]) + "." + t.rsplit(dec, 1)[1])
        elif "," in t or "." in t:
            sep = "," if "," in t else "."
            if t.count(sep) == 1:
                whole, frac = t.split(sep)
                out.append(whole + "." + frac)                         # decimal reading
                if len(frac) == 3:
                    out.append(whole + frac)                           # thousand reading ("1.500")
            else:
                out.append(t.replace(sep, ""))
        else:
            out.append(t)
    res = []
    for c in out:
        try:
            res.append(float(c))
        except ValueError:
            pass
    return res


def numbers_in(text: str, unit: str) -> list[float]:
    """Numbers of `text` that carry `unit` ('eur', 'pct', 'km', 'months', 'days'); dates are not numbers."""
    work = DATE_TOKEN_RE.sub(" ", text)
    out = []
    for m in NUM_RE.finditer(work):
        after = work[m.end():m.end() + 14]
        before = work[max(0, m.start() - 6):m.start()]
        ok = re.match(rf"\s?{UNIT_RE[unit]}", after, re.I) is not None
        if unit == "eur" and not ok:
            ok = re.search(r"(?:€|eur|euros?)\s*$", before, re.I) is not None
        if ok:
            out += _candidates(m.group(0), unit)
    return out


def unit_of(ftype: str, path: str) -> Optional[str]:
    if ftype.startswith("number EUR"):
        return "eur"
    if ftype.startswith("number percent"):
        return "pct"
    if ftype.startswith("integer km"):
        return "km"
    if ftype.startswith("integer days"):
        return "days"
    if ftype.startswith("integer"):
        return "months"
    return None


def _whole_word(needle: str, hay: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", hay) is not None


def value_in_snippet(ftype: str, value, snippet: str, path: str = "") -> Optional[str]:
    """None if the (typed) value is stated by the snippet in the right sense; else the reason it is not."""
    if ftype.startswith(("number", "integer")):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "value is not a number"
        unit = unit_of(ftype, path)
        if unit is None:
            return "unsupported numeric field"
        if not any(abs(n - float(value)) < 1e-6 for n in numbers_in(snippet, unit)):
            return f"the value {value} does not appear in the snippet with its unit ({unit})"
        return None
    if ftype.startswith("date"):
        if not isinstance(value, _dt.date) or isinstance(value, _dt.datetime):
            return "value is not a date"
        if (value.year, value.month, value.day) not in dates_in(snippet):
            return f"the date {value} does not appear in the snippet"
        return None
    v, sn = fold(str(value)), fold(snippet)
    if ftype.startswith("enum"):
        words = [v] + [fold(w) for w in SYNONYMS.get(str(value), ())]
        if any(len(w) >= 2 and _whole_word(w, sn) for w in words if w):
            return None
        return f"the value {value!r} (or a known synonym) does not appear as a word in the snippet"
    if len(v) < MIN_STRING:
        return f"the value is too short to be verified (< {MIN_STRING} characters)"
    return None if _whole_word(v, sn) else f"the value {value!r} does not appear as a whole word in the snippet"


def injection_near(text: str, snippet: str) -> Optional[str]:
    """The snippet's sentence (or line) and the one before and the one after must not read like an instruction.
    `text` is the redacted document text with its line breaks; the snippet may differ from it in whitespace only."""
    words = snippet.split()
    if not words:
        return None
    m = re.search(r"\s+".join(re.escape(w) for w in words), text, re.I)
    if not m:
        return None
    bounds = [0] + [b.end() for b in re.finditer(r"[.!?;]\s+|\n", text)] + [len(text)]
    idx = max(k for k, b in enumerate(bounds) if b <= m.start())
    lo = bounds[max(0, idx - 1)]
    hi = bounds[min(len(bounds) - 1, idx + 2)]
    window = text[lo:max(hi, m.end())]
    return "the snippet sits next to instruction-like text (possible prompt injection)" if INSTRUCTION_RE.search(window) else None


DOC_MARKER_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override)\b.{0,40}\b(?:previous|above|prior|instructions?|rules?|system)\b|"
    r"\b(?:note\s+to\s+(?:the\s+)?(?:model|assistant|ai|llm)|(?:extraction|language)\s+model|system\s+prompt|"
    r"you\s+(?:must|are|should|will)\b|as\s+an?\s+(?:ai|assistant)|new\s+instructions?|assistant\s*:|\bllm\b|"
    r"set\s+(?:the\s+)?[\w.]+(?:\s+\w+)?\s+to\b|please\s+(?:set|change|update|report|output)|"
    r"ignorez?\b.{0,40}\b(?:instructions?|consignes?|pr[ée]c[ée]dent\w*)|\bconsignes?\b|\bmod[èe]le\s+(?:d['’]extraction|de\s+langage)|"
    r"\bvous\s+devez\b|\btu\s+dois\b|\bveuillez\s+(?:mettre|modifier|d[ée]finir|indiquer)\b)", re.I)


def scan_document(text: str) -> list[str]:
    """Instruction-like passages ANYWHERE in the document (not only near a snippet): their presence taints every field
    extracted from it. Returns short excerpts for the user."""
    return [re.sub(r"\s+", " ", text[max(0, m.start() - 20):m.end() + 40]).strip() for m in DOC_MARKER_RE.finditer(text)][:5]
