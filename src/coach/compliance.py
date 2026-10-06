"""Post-generation compliance check of LLM-written text (E11-5).

The coach may explain budgeting, spending and saving habits. It must not recommend a specific investment product (FR: AMF / conseil en
investissements financiers; IT: Consob). The system prompt says so, and this module CHECKS the answer after it was written:

* ``isin``            an ISIN (two letters + nine alphanumerics + a check digit, Luhn-validated: a random 12-character code is not flagged)
* ``product_name``    a named fund / ETF / issuer / index tracker / ticker / crypto asset from a closed list
* ``recommendation``  "buy / invest in / place your money in ... <a kind of product>" (EN, FR, IT), or "vous devriez placer"

A flagged text gets a visible banner (:func:`banner`) and is recorded (``compliance_events`` + the insight's ``compliance`` column).
The check is deliberately conservative: a false positive costs one banner, a miss costs a recommendation without one. It never
rewrites the text and never blocks it.

Pure functions, no I/O except :func:`record`.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from coach import disclaimers

CODES = ("isin", "product_name", "recommendation")


@dataclass(frozen=True)
class Flag:
    code: str
    snippet: str

    def as_dict(self) -> dict:
        return {"code": self.code, "snippet": self.snippet}


# ---------------------------------------------------------------- ISIN

_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")


def isin_valid(code: str) -> bool:
    """ISO 6166 check digit: letters become two digits (A=10 ... Z=35), then the Luhn algorithm over the whole string."""
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", code):
        return False
    digits = "".join(str(int(c, 36)) for c in code)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return total % 10 == 0


# ---------------------------------------------------------------- product names (closed lists: a named product, issuer or ticker)

# issuers / fund families, indices that are the name of a tracker, crypto assets (generic words such as "ETF" or "fund" alone do not flag:
# a budget talk may use them in passing; they flag together with a buy / invest verb, see _RECO_RES)
# issuers are context, not products ("PEE géré par Amundi"): they flag only next to a product noun (ISSUERS); indices and crypto flag alone
ISSUERS = ("ishares", "vanguard", "amundi", "lyxor", "xtrackers", "spdr", "bnp paribas easy")
PRODUCT_NAMES = (
    "msci world", "msci acwi", "msci emerging", "s&p 500", "s&p500", "nasdaq 100", "nasdaq-100", "ftse all-world",
    "ftse all world", "stoxx 600", "euro stoxx", "eurostoxx", "ftse mib",
    "bitcoin", "ethereum", "dogecoin",
)
TICKERS = ("VWCE", "VWRL", "IWDA", "SWDA", "CW8", "EWLD", "MWRD", "SPY", "VOO", "VTI", "QQQ", "IVV", "EUNL", "SXR8", "CSPX", "PE500",
           "PANX", "PAEEM", "BTC", "ETH", "XRP", "DOGE", "AAPL", "MSFT", "TSLA", "NVDA", "AMZN", "GOOGL", "META")
_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(TICKERS) + r")(?![A-Za-z0-9])")

# kinds of products (generic nouns): flagged only together with a buy / invest verb
_PRODUCT_NOUN = (r"(?:etfs?|(?:index\s+|mutual\s+|investment\s+)?funds?|fonds(?:\s+indiciels?)?|fondi?\s+indicizzat\w+|sicav|opcvm|trackers?|shares|stocks|equit(?:y|ies)|bonds?|obligations?|obbligazion\w*|"
                 r"buoni\s+(?:postali|fruttiferi)|buono\s+fruttifero|titoli\s+di\s+stato|(?-i:BTP|BOT|CCT|PEA|PER)(?![a-z])|"
                 r"actions|azioni|azionari\w*|fondi?|crypto\w*|bitcoins?|ethereum|scpi|assurances?[- ]vie|unit[ée]s? de compte|"
                 r"structured products?|produits? structur[ée]s?|certificati|gold|oro|commodit\w+|forex|cfds?|warrants?|futures)")
_GAP = r"(?:\s+[\w'’\-&.%]+){0,6}?\s+"
_BUY = (r"(?:buy|buying|purchase|purchasing|acquire|go long|achet\w*|acquier\w*|souscri\w*|acquist\w*|compra\w*|sottoscriv\w*)")
_INVEST = (r"(?:invest(?:ing|ed)?(?:\s+(?:in|into))?|mett\w+\b[\w\s€$%.,'’]{0,40}?\b(?:dans|en|sur)|metti\b[\w\s€$%.,'’]{0,40}?\b(?:in|su)|put\s+(?:your|the|some)\s+money\s+(?:in|into)|allocate\s+(?:\w+\s+)?(?:to|into)|"
           r"investi\w*(?:\s+(?:dans|en|sur))?|plac\w+\s+(?:votre\s+argent\s+)?(?:dans|en|sur)|"
           r"investire(?:\s+(?:in|su))?|investi\s+(?:in|su)|investite\s+(?:in|su)|metti\s+(?:i\s+tuoi\s+soldi|il\s+denaro)\s+(?:in|su))")
_RECO_RES = [
    # buy ... <product noun>
    re.compile(r"\b" + _BUY + _GAP + _PRODUCT_NOUN + r"\b", re.I),
    # invest in / investir dans / placer ... <product noun>
    re.compile(r"\b" + _INVEST + _GAP + _PRODUCT_NOUN + r"\b", re.I),
    # "vous devriez placer / investir", "il faut placer", "you should invest", "dovresti investire"
    re.compile(r"\b(?:vous\s+devriez|vous\s+devez|il\s+faut|il\s+faudrait)\s+(?:plac\w+|investir|souscrire)\b", re.I),
    re.compile(r"\byou\s+should\s+(?:invest(?!\s+(?:more\s+)?(?:time|effort|energy|attention|thought|in\s+(?:yourself|your\s+(?:education|health|home|skills|future|career|family))))|put\s+(?:your|the)\s+money)\b", re.I),
    # advice verbs followed by a kind of product: "je vous conseille un ETF World", "consider a low-cost index fund", "I suggest bonds"
    re.compile(r"\b(?:consider|conseill\w+|recommand\w+|sugg[eè]r\w+|suggest\w*|consigli\w+|propose|look\s+at)\b" + _GAP + _PRODUCT_NOUN + r"\b", re.I),
    re.compile(r"\b(?:dovresti|dovreste|conviene|ti\s+consiglio\s+di)\s+(?:investire|sottoscrivere)\b", re.I),
    re.compile(r"\bi\s+(?:recommend|suggest)\s+(?:investing|you\s+invest)\b", re.I),
    re.compile(r"\bje\s+(?:vous\s+)?(?:recommande|conseille|suggère)\s+(?:d'|de\s+)?(?:investir|placer|souscrire)\b", re.I),
]


_SAFE_SAVINGS = re.compile(r"\b(?:livret(?:\s+[a-z])?|LEP|LDDS|LDD|compte\s+(?:[ée]pargne|sur\s+livret)|libretto|conto\s+(?:deposito|di\s+risparmio)|savings\s+account|emergency\s+fund)\b",
                           re.I)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _snip(text: str, m: re.Match, width: int = 40) -> str:
    a, b = max(0, m.start() - width // 2), min(len(text), m.end() + width // 2)
    return re.sub(r"\s+", " ", text[a:b]).strip()[:100]


def check(text: str) -> list[Flag]:
    """Every compliance flag found in `text` (empty list = nothing found). At most one flag per (code, snippet)."""
    t = _fold(text)
    out: list[Flag] = []
    seen: set[tuple] = set()

    def add(code: str, snippet: str) -> None:
        key = (code, snippet.lower())
        if key not in seen:
            seen.add(key)
            out.append(Flag(code, snippet))

    for m in _ISIN_RE.finditer(t):
        if isin_valid(m.group(1)):
            add("isin", m.group(1))
    low = t.lower()
    for name in PRODUCT_NAMES:
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(name.strip()) + r"(?![a-z0-9])", low):
            add("product_name", _snip(t, m))
            break
    named = [n for n in ISSUERS if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", low)]
    for name in ISSUERS:                              # an issuer is a product only next to a product noun, or when several are named side by side
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", low):
            ctx = t[max(0, m.start() - 50):m.end() + 50]
            if len(named) >= 2 or re.search(r"\b" + _PRODUCT_NOUN + r"\b", ctx, re.I):
                add("product_name", _snip(t, m))
                break
    tickers = list(_TICKER_RE.finditer(t))
    for m in tickers:
        ctx = t[max(0, m.start() - 60):m.end() + 60]
        near_product = bool(re.search(r"\b" + _PRODUCT_NOUN + r"\b|\b(?:buy|invest\w*|achet\w*|acquist\w*|compra\w*|etf)\b", ctx, re.I))
        if len(tickers) >= 2 or near_product:                            # "META VERIFIED 14.99" is a merchant, a lone ticker needs a product context
            add("product_name", _snip(t, m))
    for rx in _RECO_RES:
        for m in rx.finditer(t):
            if _SAFE_SAVINGS.search(t[m.start():m.end() + 80]):          # "placer ... sur un livret A": a regulated savings account, not an investment product
                continue
            add("recommendation", _snip(t, m))
            break
    return out


def codes(flags: list[Flag]) -> list[str]:
    return list(dict.fromkeys(f.code for f in flags))


# ---------------------------------------------------------------- language and banner

_LANG_WORDS = {
    "fr": ("le", "la", "les", "des", "vous", "votre", "vos", "est", "pour", "dans", "que", "une", "sur", "avec", "mois", "dépenses",
           "épargne", "pas", "ce", "ces", "et"),
    "it": ("il", "gli", "una", "per", "con", "che", "non", "sono", "della", "delle", "nel", "tuo", "tuoi", "spese", "risparmio",
           "mese", "mesi", "anche", "più", "ti", "di", "consiglio", "dovresti", "investire", "fondi", "azioni", "acquistare"),
    "en": ("the", "and", "you", "your", "for", "with", "this", "that", "are", "was", "spending", "savings", "month", "of", "to"),
}


def detect_lang(text: str) -> str:
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœæáíóúìò']+", (text or "").lower())
    if not words:
        return "en"
    score = {lang: sum(1 for w in words if w in set(ws)) for lang, ws in _LANG_WORDS.items()}
    best = max(score, key=lambda k: (score[k], k == "en"))
    return best if score[best] > 0 else "en"


def banner(flags_or_codes, lang: str = "en") -> str:
    """The banner for a flagged text ('' when there is nothing to say). `lang` fr / it / en."""
    return disclaimers.get("investment_banner", lang) if flags_or_codes else ""


def label(lang: str = "en", short: bool = False) -> str:
    return disclaimers.get("ai_label_short" if short else "ai_label", lang)


def assess(text: str, lang: str | None = None) -> dict:
    """{'ai_generated': True, 'label', 'flagged', 'flags': [{code, snippet}], 'codes', 'banner', 'lang'}: what a screen needs."""
    flags = check(text)
    lang = lang or detect_lang(text)
    return {"ai_generated": True, "label": label(lang), "label_short": label(lang, short=True), "lang": lang,
            "flagged": bool(flags), "flags": [f.as_dict() for f in flags], "codes": codes(flags), "banner": banner(flags, lang)}


def cli_block(text: str, lang: str | None = None) -> tuple[str, str]:
    """(header, footer) to print around an LLM text on the terminal: the AI label first, the banner (if any) last."""
    a = assess(text, lang)
    head = f"[{a['label_short']}] {a['label']}"
    foot = f"[!] {a['banner']}" if a["flagged"] else ""
    return head, foot


# ---------------------------------------------------------------- the record

def record(con, *, source: str, flags: list[Flag], insight_id: str | None = None, lang: str = "en") -> int | None:
    """Store a compliance event (what was flagged, where). Returns its id, None when there was nothing to record or the table is missing."""
    if not flags:
        return None
    import datetime as dt
    try:
        cur = con.execute("""INSERT INTO compliance_events(ts, source, insight_id, codes, flags, lang)
                             VALUES (?,?,?,?,?,?)""",
                          (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), source, insight_id,
                           json.dumps(codes(flags)), json.dumps([f.as_dict() for f in flags], ensure_ascii=False), lang))
        con.commit()
        return cur.lastrowid
    except Exception:                                                              # noqa: BLE001  (migrations pending)
        return None
