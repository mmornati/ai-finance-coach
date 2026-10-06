"""Redaction of a document's text before it can be sent to an LLM (E3-5).

Principles: personal data is removed FIRST (so amount protection can never shield a dotted date or a phone number);
amounts, percentages and durations are then set aside so no generic rule eats them; person-like name sequences
next to person cues are removed even when the person is not a household member (over-redacting a name is cheaper
than leaking one); organisation names (SA, SAS, BANQUE, CREDIT, ASSURANCE...) are kept. Best effort: the dry run that
shows the exact payload is the control."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from coach.classify import redact as R

L = r"[^\W\d_]"                                   # a Unicode letter
CAP = r"[A-ZÀ-ÖØ-Þ]"                              # an upper-case Unicode letter
LOW = r"[a-zß-öø-ÿ]"
WORD = rf"{CAP}(?:{L}|['’-])*"                    # Jean-Pierre, DUPONT, Élodie, O'Neil, MARTIN-LEROY
PART = r"(?:de[ \t]+la|de|du|des|d['’]|van[ \t]+der|van[ \t]+den|van|von|di|da|del|della|dei|le|la|el|al|ben|ter|ten)"
NAME = rf"{WORD}(?:[ \t]+(?:{PART}[ \t]+|{PART}(?=['’]))?{WORD}){{0,3}}"
NAMES = rf"{NAME}(?:[ \t]+(?:et|and|&|ou)[ \t]+{NAME})*"

MONTHS = ("janvier|janv|février|fevrier|févr|fevr|fév|fev|mars|avril|avr|mai|juin|juillet|juil|août|aout|septembre|sept|"
          "octobre|oct|novembre|nov|décembre|decembre|déc|dec|january|jan|february|feb|march|mar|april|apr|may|june|jun|"
          "july|jul|august|aug|september|sep|october|november|december")
DATE_ANY = (rf"(?:\d{{1,2}}[/.\-]\d{{1,2}}[/.\-]\d{{2,4}}|\d{{4}}-\d{{2}}-\d{{2}}|"
            rf"\d{{1,2}}(?:er)?\s+(?:{MONTHS})\.?\s+\d{{2,4}})")
CITY_WORD = rf"(?:(?i:d|l)['’])?{CAP}[A-Za-zÀ-ÿ'’]+"
UNITS = r"(?:EUR|euros?|€|km|kms|kilom\w*|mois|months?|ans?|years?|jours?|days?|%)"
CITY = rf"(?!(?i:{UNITS})\b){CAP}[A-Za-zÀ-ÿ'’]+(?:[ \-](?:(?i:d|l)['’]|(?i:de|du|des|la|le|sur|en|lès|lez|sous)[ \-])?{CAP}[A-Za-zÀ-ÿ'’]+){{0,3}}(?:[ \t]+(?i:cedex)(?:[ \t]+\d{{1,2}})?)?"
PLACE = rf"{CAP}{L}*(?:[ \-]{CAP}{L}*){{0,3}}"

BORN = r"n[ée](?:\(e\)|e|\(es?\))?"               # né, née, né(e)
DOB_RE = re.compile(rf"(?:\b{BORN}\s+le|\bborn\s+on|\bnat[oa]\s+il|\bdate\s+de\s+naissance|\bdate\s+of\s+birth)\s*:?\s*"
                    rf"{DATE_ANY}(?:\s*(?:à|a|au|in)\s+{PLACE}(?:\s*\(\d{{2,3}}\))?)?", re.I)
PLACE_OF_BIRTH_RE = re.compile(rf"\b(?:lieu\s+de\s+naissance|place\s+of\s+birth|{BORN}\s+[àa](?=\s))\s*:?\s*{PLACE}", re.I)
# maiden name: "Claire DURAND née MARTIN", "épouse LEROY", "veuve DUPONT"
MAIDEN_RE = re.compile(rf"(?i:\b(?:{BORN}|[ée]pouse|veuve|epouse|born))[ \t]+(?!(?i:le|la|à|a|au|on|in)\b)({NAME})")
TITLE_CS = re.compile(rf"(?<![\w])(?:M\.|Mme\.?|Mlle\.?|Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Me\.?|Pr\.?|MM\.?|Mmes\.?|Sig\.ra|Sig\.na|Sig\.|Dott\.ssa|Dott\.|"
                      rf"Ing\.|Avv\.|Hr\.|Fr\.|Herr|Frau|Fräulein|M(?=\s+{CAP}))[ \t]*({NAME})")
TITLE_WORDS = re.compile(r"(?<![\w])(?i:monsieur|madame|mademoiselle|signore|signora|signorina|herr|frau|mme|mlle|docteur|dottore|dottoressa|ma[iî]tre|professeur|m)(?:\.|\b)[ \t]*")
FUNCTION_WORDS = {"le", "la", "les", "de", "du", "des", "et", "ou", "pour", "a", "à", "au", "aux", "est", "sont", "ne", "n",
                  "vous", "nous", "je", "il", "elle", "ils", "qui", "que", "ce", "cet", "cette", "dans", "par", "sur", "avec",
                  "sans", "en", "un", "une", "the", "of", "and", "is", "to", "for", "in", "at", "on", "was", "has", "will"}
ORG_TOKENS = {  # generic organisation signals only: brand names collide with surnames (LEROY, MARTIN...)
    "SA", "SAS", "SASU", "SARL", "EURL", "SCI", "SNC", "SCOP", "SPA", "SRL", "GMBH", "LTD", "LLC", "INC", "PLC", "AG", "BV", "NV",
    "BANQUE", "BANK", "BANCA", "CREDIT", "CRÉDIT", "FINANCE", "FINANCES", "FINANCEMENT", "CAISSE", "ASSURANCE", "ASSURANCES",
    "MUTUELLE", "GROUPE", "GROUP", "SOCIETE", "SOCIÉTÉ", "COMPAGNIE", "AGENCE", "CABINET", "ETABLISSEMENTS", "ÉTABLISSEMENTS",
    "LEASING", "EPARGNE", "ÉPARGNE", "SERVICES", "SYNDIC", "SYNDICAT", "MAIRIE", "ASSOCIATION", "FONDATION", "MUTUELLES",
    "CAPITAL", "PARTNERS", "HOLDING", "COOPERATIVE", "COOPÉRATIVE", "CAF", "CPAM", "URSSAF",
    # well-known lenders / insurers / banks (names that are not generic words but are never a person here)
    "CETELEM", "SOFINCO", "COFIDIS", "GENERALI", "AXA", "ALLIANZ", "MAAF", "MACIF", "MATMUT", "CARDIF", "CNP",
    "FLOA", "FRANFINANCE", "DIAC", "STELLANTIS", "SANTANDER", "ONEY", "AVIVA", "GROUPAMA", "MMA", "PACIFICA",
    "MAIF", "GMF", "APRIL", "SWISSLIFE", "AFER", "SPIRICA", "PREDICA", "HSBC", "UNICREDIT", "INTESA", "SANPAOLO", "BNP", "PARIBAS",
    "LCL", "CIC", "FORTUNEO", "BOURSORAMA", "REVOLUT", "N26", "COFINOGA", "FINAREF", "CGL", "PSA", "RCI", "VOLKSWAGEN", "TOYOTA",
    "MERCEDES", "BMW", "RENAULT", "AGRICOLE", "POPULAIRE", "POSTALE", "MUTUEL", "HELLOBANK"}


CUES_BEFORE = (r"(?i:demeurant|domicili[ée]e?s?|emprunteurs?|co-?emprunteurs?|souscripteurs?|co-?souscripteurs?|assur[ée]e?s?|"
               r"titulaires?|co-?titulaires?|[ée]poux|[ée]pouse|conjoint|conjointe|mandataire|caution|garant|repr[ée]sent[ée]e? par|"
               r"borrowers?|co-?borrowers?|account\s+holders?|policy\s*holders?|insured|signataires?|client|cliente|contact|"
               r"entre|between|nom|pr[ée]nom|name|n[ée]\(?e?\)?\s+de|fils\s+de|fille\s+de)")
CUES_AFTER = (r"(?i:demeurant|domicili[ée]e?s?|n[ée](?:\(e\)|e|\(es?\))?\s+le|ci-apr[èe]s|agissant|residing|residing\s+at|"
              r"dont\s+le\s+domicile|titulaires?|emprunteurs?|co-?emprunteurs?|souscripteurs?)")
CUE_BEFORE_RE = re.compile(rf"{CUES_BEFORE}\b[ \t]*:?[ \t]*({NAMES})")
CUE_AFTER_RE = re.compile(rf"({NAMES})[ \t,]+(?={CUES_AFTER}\b)")
# two multi-word names joined by "et": "Jean-Pierre DUPONT et Élodie MARTIN-LEROY"
PAIR_RE = re.compile(rf"(?<![\w])({WORD}(?:[ \t]+{WORD})+)[ \t]+(?i:et|and)[ \t]+({WORD}(?:[ \t]+{WORD})+)(?![\w-])")
NIR_RE = re.compile(r"(?<!\d)[12][ .]?\d{2}[ .]?(?:0[1-9]|1[0-2]|[2-9]\d)[ .]?(?:\d{2}|2[AB])[ .]?\d{3}[ .]?\d{3}(?:[ .]?\d{2})?(?!\d)")
PAN_RE = re.compile(r"(?<![\d.,])(?:\d[ -]?){12,18}\d(?![\d.,])")
RUM_RE = re.compile(r"\b(?:RUM|r[ée]f[ée]rence\s+unique\s+de\s+mandat|unique\s+mandate\s+reference|mandate\s+(?:id|ref(?:erence)?))\b[ \t:]*"
                    r"([+*A-Za-z0-9][+*A-Za-z0-9\-_/.]{4,})", re.I)
BIC_RE = re.compile(r"\b(?:BIC|SWIFT)\b[ \t:]*([A-Z]{4}[ ]?[A-Z]{2}[ ]?[A-Z0-9]{2}(?:[ ]?[A-Z0-9]{3})?)\b", re.I)
# a reference after a label, possibly with spaces: "PR 4512 7788", "N° de prêt : 7654321E", "Réf. dossier: AB-2020/123456"
REF_VALUE = (r"((?:[A-Za-z]{1,5}[ \-/]?)?[A-Za-z0-9]*\d[A-Za-z0-9]*(?:[ \-/.](?:(?-i:[A-Z]{1,5})(?![A-Za-z])|[A-Za-z0-9]*\d[A-Za-z0-9]*))*)")
REF_LABEL_RE = re.compile(r"(?:\b(?:r[ée]f(?:[ée]rence)?|num[ée]ro|dossier|contrat|pr[êe]t|compte|client|policy|loan|account|"
                          r"reference|no)\b|\bn[°º])\.?[ \t]*(?:de\s+|du\s+|d['’]|of\s+|no\.?\s*)?(?:pr[êe]t|dossier|contrat|compte|client|loan|account|"
                          r"police)?[ \t]*[:#]?[ \t]*" + REF_VALUE, re.I)
GENERIC_REF_RE = re.compile(r"\b(?=[A-Za-z0-9/-]*\d)(?=[A-Za-z0-9/-]*[A-Za-z])[A-Za-z0-9]{2,}(?:[-/][A-Za-z0-9]+)+\b")
PHONE_FR_RE = re.compile(r"(?<![\w])(?:\+33|0033|0)[ .\-]?[1-9](?:[ .\-]?\d{2}){4}(?![\w])")
# amounts, percentages, distances and durations: set aside so the generic number rules never touch them
KEEP_RE = re.compile(
    r"(?<![\w.,])\d+(?:[ \u00a0\u202f.]\d{3})*(?:[,.]\d{1,4})?(?=\s?(?:€|EUR\b|euros?\b|%|km\b|kms\b|mois\b|months?\b|ans?\b|years?\b|jours?\b|days?\b))|"
    r"(?<![\w.,])\d{1,3}(?:[ \u00a0\u202f]\d{3})*,\d{2}(?![\d.,])|(?<![\w.,\d])\d+[.,]\d{2}(?![\d.,])", re.I)
STREET_RE = re.compile(r"\b\d{1,4}\s*(?:bis|ter)?[, ]+\s*(?:rue|avenue|av\.?|boulevard|bd|chemin|impasse|place|all[ée]e|route|"
                       r"quai|cours|via|viale|piazza|corso|vicolo|street|st\.|road|rd\.?|lane)\b[^\n,;]{0,60}", re.I)
POSTCODE_RE = re.compile(rf"(?<![€$] )(?<![\d.,€$])\b\d{{5}}[ \t]+{CITY}(?![\d,])")
BOX_RE = re.compile(r"\b(?:BP|B\.P\.|CS|TSA|CEDEX)[ \t]*\d{1,6}\b", re.I)
IBAN_ANY_RE = re.compile(r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Z0-9]{4}[ ]?){2,7}[A-Z0-9]{1,4}\b", re.I)
PHONE_INTL_RE = re.compile(r"(?<![\w])(?:\+|00)\d{1,3}[ .\-]?(?:\(?\d{1,4}\)?[ .\-]?){2,5}\d{2,4}(?![\w])")
PHONE_LABEL_RE = re.compile(r"(?i:\b(?:t[ée]l(?:[ée]phone)?|cell(?:ulare)?|mobile|portable|fax|phone|handy)\b\.?)[ \t]*:?[ \t]*([+\d][\d +().\-]{5,}\d)")
ID_LABEL_RE = re.compile(r"(?i:\b(?:matricule|employee\s*(?:no|id|number)|personalnummer|codice\s+fiscale|tax\s*id|siret|siren|"
                         r"n°\s*client|customer\s*(?:no|id|number)|kundennummer|numero\s+cliente)\b)[ \t]*[:#]?[ \t]*([A-Za-z0-9][A-Za-z0-9 .\-]{3,})")
CODICE_FISCALE_RE = re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b")
# a number after an amount label is kept even without a unit ("Capital emprunté : 250000")
AMOUNT_LABEL_RE = re.compile(r"(?i:\b(?:capital|montant|prix|mensualit[ée]s?|emprunt[ée]?|financ[ée]|valeur\s+r[ée]siduelle|loyer|"
                             r"[ée]ch[ée]ance|apport|principal|amount|payment|rate|taux|taeg|dur[ée]e|kilom[ée]trage|"
                             r"franchise|cotisation|prime|solde|total)\b)[^\n\d\x00]{0,40}?((?:\d{1,3}(?:[ \u00a0\u202f.]\d{3})+|\d+)(?:[,.]\d{1,4})?)")
SIGNATURE_CUE_RE = re.compile(r"(?i:\b(?:signature|sign[ée]e?\s+par|signed\s+by|lu\s+et\s+approuv[ée]|bon\s+pour\s+accord|"
                              r"firma|unterschrift|fait\s+[àa]|made\s+in|le\s+soussign[ée]e?|je\s+soussign[ée]e?)\b)[ \t]*[:,]?[ \t]*")
LABEL_LOWER_CUE_RE = re.compile(r"(?i:\b(?:contact|nom|pr[ée]nom|name|client|cliente|emprunteur|co-?emprunteur|souscripteur|"
                                r"titulaire|assur[ée]e?|b[ée]n[ée]ficiaire|garant|caution|mandataire)\b)[ \t]*[:,][ \t]*")
TABLE_HEAD_RE = re.compile(r"(?i)^\s*(?:nom|name|surname|last\s*name|cognome|familienname)\s*([|;\t])\s*(?:pr[ée]nom|first\s*name|given\s*name|nome|vorname)")
TABLE_HEAD_REV_RE = re.compile(r"(?i)^\s*(?:pr[ée]nom|first\s*name|given\s*name|nome|vorname)\s*([|;\t])\s*(?:nom|name|surname|last\s*name|cognome|familienname)")

EMAIL_RE = R.EMAIL_RE


@dataclass
class Redacted:
    text: str
    counts: dict[str, int] = field(default_factory=dict)


def _is_org(text: str) -> bool:
    toks = {t.upper() for t in re.split(r"[^\wÀ-ÿ]+", text) if t}
    return bool(toks & ORG_TOKENS)


def _given_name(word: str) -> bool:
    return fold_upper(word) in _GIVEN


def fold_upper(w: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", w.upper()) if unicodedata.category(c) != "Mn")


def _load_given():
    from coach.classify.parsers.common import FIRST_NAMES
    extra = {"ANNA", "LUCA", "GIOVANNI", "ANDREA", "FRANCESCO", "GIUSEPPE", "ALESSANDRO", "PAOLO", "STEFANO", "HANS",
             "JURGEN", "KLAUS", "PETER", "THOMAS", "MICHAEL", "ANDREAS", "WOLFGANG", "UWE", "STEFAN", "SABINE", "PETRA", "ANKE",
             "JAN", "PIET", "WILLEM", "JOHN", "JAMES", "ROBERT", "WILLIAM", "DAVID", "MARY", "SARAH", "EMMA", "OLIVIA", "ELODIE", "JEANPIERRE"}
    return {fold_upper(n) for n in FIRST_NAMES} | extra


_GIVEN = _load_given()


def _read_words(t: str, j: int, maxn: int = 4, lenient: bool = True) -> tuple[list[tuple[int, int]], int]:
    """Read up to `maxn` consecutive name-like words from position j (letters, apostrophes, hyphens; optional
    particles like de / van / di between them). Stops at function words, organisations, digits and punctuation. With
    `lenient` lowercase words count (a signature typed in lower case)."""
    spans = []
    while len(spans) < maxn:
        w = re.match(rf"({L}(?:{L}|['’-])*)", t[j:])
        if not w:
            break
        word = w.group(1)
        lw = word.lower()
        if _is_org(word) or (lw in FUNCTION_WORDS and lw not in ("de", "du", "van", "von", "di", "da", "del", "della", "le", "la")):
            break
        if lw in ("de", "du", "van", "von", "di", "da", "del", "della", "le", "la") and spans:
            nxt = re.match(rf"[ \t]+(?:(?:la|der|den)[ \t]+)?({L}+)", t[j + len(word):])
            if not nxt:
                break
        elif not (word[0].isupper() or lenient):
            break
        spans.append((j, j + len(word)))
        j += len(word)
        sp = re.match(r"[ \t]+(?=" + L + ")", t[j:])
        if not sp:
            break
        j += sp.end()
    return spans, j


def _redact_group(group: str) -> str:
    """A cue captured "A et B": each side is judged alone (an organisation stays, a person goes)."""
    parts = re.split(r"([ \t]+(?:et|and|&|ou)[ \t]+)", group)
    return "".join(p if i % 2 or _is_org(p) else "[NAME]" for i, p in enumerate(parts))


def _title_names(t: str, counts) -> str:
    """After a title (Monsieur, Mme, Mlle., Sig.ra, Herr...) redact the following name words: Capitalised words, or
    lowercase ones when the title itself is written in lowercase; function words, numbers and punctuation end the name."""
    out, pos = [], 0
    for m in TITLE_WORDS.finditer(t):
        if m.start() < pos:
            continue
        lowered_title = m.group(0).strip(". \t").islower()
        spans, _ = _read_words(t, m.end(), 3, lenient=lowered_title)
        if spans:
            out.append(t[pos:spans[0][0]])
            out.append("[NAME]")
            pos = spans[-1][1]
            counts["titled name"] = counts.get("titled name", 0) + 1
    out.append(t[pos:])
    return "".join(out)


def _regex_names(t: str, rx, counts, label: str, lenient: bool = True) -> str:
    """After each match of cue regex `rx`, redact the name words that follow (any case when `lenient`)."""
    out, pos = [], 0
    for m in rx.finditer(t):
        if m.end() < pos:
            continue
        spans, _ = _read_words(t, m.end(), 4, lenient=lenient)
        if spans and not _is_org(t[spans[0][0]:spans[-1][1]]):
            out.append(t[pos:spans[0][0]])
            out.append("[NAME]")
            pos = spans[-1][1]
            counts[label] = counts.get(label, 0) + 1
    out.append(t[pos:])
    return "".join(out)


def _place_after(t: str, rx, counts) -> str:
    out, pos = [], 0
    for m in rx.finditer(t):
        if m.end() < pos:
            continue
        spans, _ = _read_words(t, m.end(), 3, lenient=True)
        if spans:
            out.append(t[pos:spans[0][0]])
            out.append("[PLACE]")
            pos = spans[-1][1]
            counts["place of signature"] = counts.get("place of signature", 0) + 1
    out.append(t[pos:])
    return "".join(out)


def _tables(t: str, counts) -> str:
    """After a `Nom | Prénom` header, the name columns of the following rows are personal data."""
    lines = t.split("\n")
    i = 0
    while i < len(lines):
        m = TABLE_HEAD_RE.match(lines[i]) or TABLE_HEAD_REV_RE.match(lines[i])
        if not m:
            i += 1
            continue
        sep = m.group(1)
        cols = {0, 1}
        i += 1
        while i < len(lines) and sep in lines[i] and lines[i].strip():
            cells = lines[i].split(sep)
            for c in cols:
                if c < len(cells) and cells[c].strip() and not _is_org(cells[c]):
                    cells[c] = cells[c].replace(cells[c].strip(), "[NAME]")
                    counts["table name"] = counts.get("table name", 0) + 1
            lines[i] = sep.join(cells)
            i += 1
    return "\n".join(lines)


def _given_name_runs(t: str, counts) -> str:
    """Untitled `Claire PETIT` / `JEAN DUPONT`: two to four capitalised or ALL-CAPS words where one is a known given
    name and none is an organisation word."""
    rx = re.compile(rf"(?<![\w-])({WORD}(?:[ \t]+(?:{PART}[ \t]+)?{WORD}){{1,3}})(?![\w-])")

    def f(m):
        words = re.findall(rf"{L}(?:{L}|['’-])*", m.group(1))
        if _is_org(m.group(1)) or not any(_given_name(w) or any(_given_name(x) for x in w.split("-")) for w in words):
            return m.group(0)
        counts["untitled name"] = counts.get("untitled name", 0) + 1
        return "[NAME]"
    return rx.sub(f, t)


def redact_document(text: str, names: set[str] | frozenset[str] = frozenset()) -> Redacted:
    counts: dict[str, int] = {}

    def sub(rx, repl, t, label):
        t, n = rx.subn(repl, t)
        if n:
            counts[label] = counts.get(label, 0) + n
        return t

    def keep_label(tag):
        def f(m):
            full = m.group(0)
            return full[: m.start(1) - m.start(0)] + tag + full[m.end(1) - m.start(0):]
        return f

    def group_names(m, grp=1):
        return m.group(0)[: m.start(grp) - m.start(0)] + _redact_group(m.group(grp)) + m.group(0)[m.end(grp) - m.start(0):]

    t = unicodedata.normalize("NFC", text)
    # 1. specific personal data first
    t = sub(DOB_RE, "[DOB]", t, "birth date / place")
    t = sub(PLACE_OF_BIRTH_RE, "[DOB]", t, "birth date / place")
    t = sub(EMAIL_RE, "[EMAIL]", t, "e-mail")
    t = sub(IBAN_ANY_RE, "[IBAN]", t, "iban")
    t = sub(R.IBAN_RE, "[IBAN]", t, "iban")
    t = sub(BIC_RE, keep_label("[BIC]"), t, "bic")
    t = sub(RUM_RE, keep_label("[MANDATE]"), t, "mandate reference")
    t = sub(NIR_RE, "[NIR]", t, "social security number")
    t = sub(CODICE_FISCALE_RE, "[ID]", t, "tax id")
    t = sub(PHONE_LABEL_RE, keep_label("[PHONE]"), t, "phone")
    t = sub(PHONE_FR_RE, "[PHONE]", t, "phone")
    t = sub(PHONE_INTL_RE, "[PHONE]", t, "phone")
    t = sub(R.PHONE_RE, "[PHONE]", t, "phone")
    t = sub(PAN_RE, "[CARD]", t, "card / long number")
    t = sub(R.HEXID_RE, "[ID]", t, "id")
    t = sub(ID_LABEL_RE, keep_label("[ID]"), t, "identifier")
    t = sub(STREET_RE, "[ADDRESS]", t, "address")
    t = sub(BOX_RE, "[ADDRESS]", t, "address")
    # 2. people: tables, titles, signatures, cues, pairs, given names
    t = _tables(t, counts)
    t = sub(TITLE_CS, lambda m: m.group(0)[: m.start(1) - m.start(0)] + "[NAME]", t, "titled name")
    t = _title_names(t, counts)
    t = sub(MAIDEN_RE, lambda m: group_names(m), t, "maiden name")
    t = _place_after(t, re.compile(r"(?i:\bfait\s+[àa]\b)[ \t]*"), counts)
    t = _regex_names(t, SIGNATURE_CUE_RE, counts, "signature name")
    t = _regex_names(t, LABEL_LOWER_CUE_RE, counts, "name after a label")
    t = sub(CUE_BEFORE_RE, lambda m: group_names(m), t, "name next to a cue")
    t = sub(CUE_AFTER_RE, lambda m: _redact_group(m.group(1)) + " ", t, "name next to a cue")
    t = sub(PAIR_RE, lambda m: "[NAME] et [NAME]" if not (_is_org(m.group(1)) or _is_org(m.group(2))) else m.group(0), t, "name pair")
    t = _given_name_runs(t, counts)
    toks = {n for n in names if n and len(n) >= 3}
    if toks:
        pat = re.compile(r"(?<![\w])(" + "|".join(re.escape(n) for n in sorted(toks, key=len, reverse=True)) + r")(?![\w])", re.I)
        t = sub(pat, "[NAME]", t, "household name")
    # 3. amounts, percentages, distances, durations are the point of the document
    kept: list[str] = []

    def keep(m):
        kept.append(m.group(0))
        return f"\x00{len(kept) - 1}\x00"

    def keep_group(m):
        kept.append(m.group(1))
        return m.group(0)[: m.start(1) - m.start(0)] + f"\x00{len(kept) - 1}\x00" + m.group(0)[m.end(1) - m.start(0):]

    t = KEEP_RE.sub(keep, t)
    t = AMOUNT_LABEL_RE.sub(keep_group, t)
    t = sub(POSTCODE_RE, "[ADDRESS]", t, "address")
    t = sub(REF_LABEL_RE, keep_label("[REF]"), t, "reference number")
    t = sub(GENERIC_REF_RE, "[REF]", t, "reference number")
    t = sub(R.LONG_DIGITS_RE, "[NUM]", t, "long number")
    t = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return Redacted(re.sub(r"\n{3,}", "\n\n", t).strip(), counts)
