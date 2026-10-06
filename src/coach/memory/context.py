"""`coach memory context` (prep for E6): a concise, privacy-aware summary of the household memory for an LLM.

Privacy rules (tested): no IBAN / e-mail / phone / long account numbers (the classifier's redaction layer is applied
to every string), no account labels, no raw bank descriptions; the household's real names (members' names and
aliases, account-holder names from the database, owner ids) are replaced by a stable pseudonym (the member id, or
``adult-1`` / ``kid-1`` when the id itself would reveal the name) and the family name by ``[family]``, unless
``names=True`` is passed explicitly (a local, human-facing use)."""
from __future__ import annotations

import copy
import datetime as dt
import re
import unicodedata
from typing import Optional

from coach.classify.redact import redact
from coach.memory import totals
from coach.memory.store import EVENT_HEADING_RE, MemoryStore

STOP = {"joint", "compte", "livret", "courant", "account", "main", "mme", "monsieur", "madame", "the", "and", "des",
        "les", "ami", "bank", "banque", "epargne", "caisse"}


def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _strip(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


class Scrubber:
    """Replaces the household's names by pseudonyms. `mapping`: lowercase token -> replacement."""

    def __init__(self, store: MemoryStore, con=None):
        members = store.members()
        raw_tokens: dict[str, set[int]] = {}          # token of a member's NAME -> members whose name has it
        alias_tokens: set[str] = set()
        self.pseudo: dict[str, str] = {}           # member id -> pseudonym
        name_tokens_all: set[str] = set()
        for i, m in enumerate(members):
            for t in {t for t in re.split(r"[^\w']+", m.name) if len(t) >= 3}:
                raw_tokens.setdefault(t.lower(), set()).add(i)
            alias_tokens |= {t.lower() for a in m.aliases for t in re.split(r"[^\w']+", a) if len(t) >= 3}
        name_tokens_all = set(raw_tokens) | alias_tokens
        counters = {"adult": 0, "child": 0}
        for m in members:
            counters[m.role] += 1
            label = "kid" if m.role == "child" else m.role           # one scheme for memory and analytics: adult-N, kid-N
            self.pseudo[m.id] = m.id if m.id.lower() not in name_tokens_all and _strip(m.id.lower()) not in {
                _strip(t) for t in name_tokens_all} else f"{label}-{counters[m.role]}"
        self.mapping: dict[str, str] = {}
        for tok, idxs in raw_tokens.items():
            self.mapping[tok] = "[family]" if len(idxs) > 1 else self.pseudo[members[next(iter(idxs))].id]
        for tok in alias_tokens - set(raw_tokens):      # only seen in a holder-name spelling ("MME X ET M Y"): a surname
            self.mapping[tok] = "[family]"
        self.aliases = sorted({a for m in members for a in m.aliases if len(a) >= 4}, key=len, reverse=True)
        # names known from the database (account holders, owner ids) and the common given names: without a declared
        # household this is the only protection, so anything that is not a declared member becomes [person]/[family]
        from coach.classify.parsers.common import FIRST_NAMES
        covered = lambda t: t in self.mapping or any(_strip(t) == _strip(k) for k in self.mapping)   # noqa: E731
        given = {n.lower() for n in FIRST_NAMES}
        if con is not None:
            from coach.classify.candidates import household_names
            fam, first = household_names(con)
            owners = {r[0] for r in con.execute("SELECT DISTINCT owner FROM accounts WHERE owner IS NOT NULL")}
            owner_toks = {t.lower() for o in owners if o.lower() != "joint" for t in re.split(r"[^\w']+", o) if len(t) >= 3}
            for t in sorted({x.lower() for x in fam | first} | owner_toks):
                if len(t) >= 3 and t not in STOP and not covered(t):
                    self.mapping[t] = "[person]" if (t in given or t in owner_toks) else "[family]"
        for t in sorted(given):
            if len(t) >= 3 and t not in STOP and not covered(t):
                self.mapping[t] = "[person]"
        self.db_names = set(self.mapping)
        for k in list(self.mapping):
            self.mapping.setdefault(_strip(k), self.mapping[k])
        keys = sorted(self.mapping, key=len, reverse=True)
        self._rx = re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w])", re.I) if keys else None
        self._arx = re.compile("|".join(re.escape(a) for a in self.aliases), re.I) if self.aliases else None

    def person(self, owner: Optional[str]) -> Optional[str]:
        """Pseudonym of an account owner value (member id / name / alias / 'joint')."""
        if owner is None or owner.lower() == "joint":
            return owner
        for k, v in self.mapping.items():
            if _strip(owner.lower()) == _strip(k):
                return v
        return self.scrub(owner)

    def scrub(self, text):
        if not isinstance(text, str) or not text:
            return text
        t = text
        if self._arx:
            t = self._arx.sub("[holder]", t)
        if self._rx:
            t = self._rx.sub(lambda m: self.mapping.get(m.group(0).lower(), self.mapping.get(_strip(m.group(0).lower()), "[person]")), t)
        return redact(t)

    def basic_deep(self, obj):
        """Only the technical redaction (IBAN, e-mail, phone, long numbers), names stay (``--names``)."""
        if isinstance(obj, str):
            return redact(obj)
        if isinstance(obj, dict):
            return {k: self.basic_deep(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.basic_deep(v) for v in obj]
        return obj

    def scrub_deep(self, obj):
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, dict):
            return {k: self.scrub_deep(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.scrub_deep(v) for v in obj]
        return obj


def _md_sections(text: str) -> list[tuple[str, str]]:
    """events.md -> [(slug id, body)] for each `## slug` heading."""
    out, cur, buf = [], None, []
    for ln in text.splitlines():
        m = EVENT_HEADING_RE.match(ln)
        if m or ln.startswith("## "):
            if cur is not None:
                out.append((cur, "\n".join(buf).strip()))
            cur, buf = (m.group(1) if m else None), []
            continue
        if cur is not None:
            buf.append(ln)
    if cur is not None:
        out.append((cur, "\n".join(buf).strip()))
    return out


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()


def public_question_text(q) -> str:
    """Generated questions about merchants name them in their own text; for an LLM they are restated from the
    evidence only (counts, amounts, dates, categories): a merchant key may be a third party's name."""
    ev, kind = q.evidence, (q.key or "").split(":", 1)[0]
    if kind == "merchant":
        return (f"An unnamed merchant: {ev.get('payments')} payment(s), {ev.get('net_total', 0):+,.0f} EUR between "
                f"{ev.get('first')} and {ev.get('last')} (currently {ev.get('current_category')}): what is it?")
    if kind == "large":
        return (f"A large payment of {ev.get('amount', 0):+,.0f} EUR on {ev.get('date')} (counted as "
                f"{ev.get('category')}): is it a one-off?")
    if kind == "recurring":
        return (f"A recurring monthly debit of about {ev.get('average', 0):,.0f} EUR ({ev.get('payments')} payments since "
                f"{ev.get('first')}, {ev.get('category')}) has no loan or contract file: what is it?")
    return q.question


_CAPS_RE = re.compile(r"[A-ZÀ-Ý][A-ZÀ-Ý'’-]{2,}(?: [A-ZÀ-Ý][A-ZÀ-Ý'’-]{2,}){0,4}")


def scrub_names_in_caps(text: str) -> str:
    """Hand-written / migrated questions name merchants in capitals; a run of capitals that holds a given name and no
    organisation word is replaced by [name]."""
    from coach.classify.parsers.common import FIRST_NAMES, ORG_WORDS, strip_accents
    from coach.classify.candidates import LEGAL_FORMS

    def f(m):
        toks = set(re.split(r"[^A-Z'-]+", strip_accents(m.group(0)).upper())) - {""}
        return "[name]" if toks & FIRST_NAMES and not toks & (ORG_WORDS | LEGAL_FORMS) else m.group(0)
    return _CAPS_RE.sub(f, text)


ONLINE_BANKS = ("revolut", "fortuneo", "n26", "boursorama", "hello bank", "monabanq", "trade republic", "qonto", "bforbank", "wise", "nickel",
                "ing direct", "lydia", "bunq")
LENDER_BY_KIND = {"mortgage": "bank", "car_loan": "car-finance company", "loa": "car-lease company", "lld": "car-lease company",
                  "consumer_loan": "consumer-credit company", "bnpl": "payment-plan provider"}
PROVIDER_BY_KIND = {"regulated_savings": "bank", "savings_account": "bank", "life_insurance_savings": "life-insurance provider",
                    "employee_savings_plan": "employee-savings provider", "securities": "broker", "pension": "pension provider",
                    "crypto": "crypto platform", "real_estate_rental": "property manager"}


def generic_bank(name: str) -> str:
    """A bank by its KIND for a model in coarse mode: no brand, no region (`Caisse d'Epargne Normandie` -> `regional bank`)."""
    from coach.analytics.regions import strip_regions
    low = (name or "").lower()
    if any(o in low for o in ONLINE_BANKS):
        return "online bank"
    return "regional bank" if strip_regions(name or "") != (name or "") else "bank"


def generalise_orgs(ctx: dict) -> None:
    """Coarse mode: lenders, insurers, providers, banks and the financed asset are replaced by what they ARE (E9: real names only in standard mode)."""
    for a in ctx["accounts"]:
        a["bank"] = generic_bank(a["bank"]) if a.get("bank") not in (None, "?") else a.get("bank")
    for x in ctx["liabilities"]:
        x["lender"] = LENDER_BY_KIND.get(x["kind"], "lender") if x.get("lender") else None
        x["asset"] = None
    for x in ctx["assets"]:
        x["provider"] = PROVIDER_BY_KIND.get(x["kind"], "provider") if x.get("provider") else None
    for x in ctx["contracts"]:
        x["provider"] = f"{(x.get('kind') or 'service').replace('_', ' ')} provider" if x.get("provider") else None


def age_band(role: str, birth_year: Optional[int], today: dt.date) -> Optional[str]:
    """What a model may know of a member's age: a coarse band, never a birth year (E14 review). None when the year is unknown."""
    if role == "adult":
        return "adult"
    if birth_year is None:
        return None
    age = today.year - birth_year
    return "under 6" if age < 6 else "6-11" if age < 12 else "teen" if age < 18 else "adult"


def build_context(store: MemoryStore, con=None, cfg=None, *, names: bool = False, coarse: bool = False,
                  today: Optional[dt.date] = None, neutral_ids: bool = True) -> dict:
    today = today or dt.date.today()
    sc = Scrubber(store, con)
    ident = sc.basic_deep if names else sc.scrub_deep
    pseudo = (lambda o: o) if names else sc.person

    members = []
    for m in store.members():
        d = {"id": m.id if names else sc.pseudo[m.id], "role": m.role}
        if names:                                       # exact birth years stay local (tax, onboarding); a model only ever gets an age band
            d.update(birth_year=m.birth_year, name=m.name, aliases=m.aliases)
        else:
            d["age_band"] = age_band(m.role, m.birth_year, today)
        members.append(d)
    accounts = []
    if con is not None:
        for bank, owner, purpose, source, n, first, last in con.execute("""
                SELECT COALESCE(a.bank,'?'), a.owner, a.purpose, a.source, COUNT(t.tx_key), MIN(t.booking_date),
                       MAX(t.booking_date) FROM accounts a LEFT JOIN transactions t ON t.account_uid=a.uid
                WHERE a.exclude=0 GROUP BY a.uid ORDER BY 1, 2"""):
            accounts.append({"bank": bank, "owner": pseudo(owner), "purpose": purpose, "source": source,
                             "transactions": n, "first": first, "last": last})
    liabs = [{"id": m.id, "kind": m.kind, "lender": m.lender, "asset": m.asset, "monthly_payment": m.monthly_payment,
              "outstanding": m.outstanding, "outstanding_as_of": m.outstanding_as_of, "end_date": m.end_date,
              "rate_nominal": m.rate.nominal if m.rate else None, "notes": m.notes or None}
             for _, m in store.liabilities()]
    assets = [{"id": a.id, "kind": a.kind, "provider": a.provider, "holder": pseudo(a.holder) if a.holder else None,
               "value": a.amount, "as_of": a.as_of, "liquidity": a.liquidity, "notes": a.notes} for a in store.assets()]
    contracts = [{"id": m.id, "provider": m.provider, "kind": m.kind, "amount": m.billing.amount if m.billing else None,
                  "period": m.billing.period if m.billing else None, "renewal": m.renewal,
                  "commitment_end": m.commitment_end, "keep": m.keep} for _, m in store.contracts()]
    structured = {e.id: e for e in store._items("events.yaml", "events")}
    events = []
    for eid, body in _md_sections(store.read_text("events.md")):
        if eid is None:
            continue
        e = structured.get(eid)
        events.append({"id": eid, "start": e.start if e else None, "end": e.end if e else None,
                       "budget": e.budget if e else None, "text": body[:700]})
    qs = [{"id": q.id, "generated": q.origin == "generated", "question": public_question_text(q) if q.origin == "generated" else scrub_names_in_caps(q.question),
           "stake": q.stake,
           "target": q.suggested_target.file if q.suggested_target else None}
          for q in sorted(store.questions(), key=lambda q: -(q.stake or 0)) if q.status == "open"]
    annotations = [{"id": a.id, "category": a.category, "tags": a.tags, "event": a.event,
                    "note": (a.note or "")[:200] or None} for a in store.annotations()]
    ctx = {
        "generated": today.isoformat(), "names_included": bool(names),
        "members": members, "accounts": accounts, "liabilities": liabs, "assets": assets,
        "manual_totals": totals.manual_totals(store, today, getattr(cfg, "memory_asset_stale_months", 3),
                                              getattr(cfg, "memory_stale_months", 6)),
        "contracts": contracts, "events": events,
        "preferences": _strip_comments(store.read_text("preferences.md")),
        "profile": _strip_comments(store.read_text("profile.md")),
        "open_questions": qs, "annotations": annotations,
    }
    if coarse:
        # free text can hold anything (towns, employers, neighbours): keep only the structured facts
        ctx["profile"] = ""
        ctx["preferences"] = ""
        for q in ctx["open_questions"]:
            if not q.get("generated"):
                q["question"] = "(question text withheld in coarse mode)"
        for e in ctx["events"]:
            e["text"] = ""
        for a in ctx["annotations"]:
            a["note"] = None
        for x in ctx["liabilities"] + ctx["assets"]:
            x["notes"] = None
        generalise_orgs(ctx)
        ctx["coarse"] = True
        terms = _coarse_terms(store) if neutral_ids else {}
        if terms:
            # an id can carry a declared place / employer ("pinel-springfield-flat"): neutral ids, consistently everywhere
            low = [t.lower() for t in terms]
            renamed: dict[str, str] = {}
            for section, prefix in (("liabilities", "liability"), ("assets", "asset"), ("contracts", "contract"),
                                    ("events", "event"), ("annotations", "annotation")):
                n = 0
                for item in ctx[section]:
                    if any(t in item["id"].lower() for t in low):
                        n += 1
                        renamed[item["id"]] = f"{prefix}-{n}"
                        item["id"] = renamed[item["id"]]
            for kind in ("assets", "liabilities"):
                for it in ctx["manual_totals"][kind]["items"]:
                    it["id"] = renamed.get(it["id"], it["id"])
                for key in ("unknown_value", "stale", "unknown_outstanding"):
                    if key in ctx["manual_totals"][kind]:
                        ctx["manual_totals"][kind][key] = [renamed.get(x, x) for x in ctx["manual_totals"][kind][key]]
            for q in ctx["open_questions"]:
                for old, new in renamed.items():
                    q["question"] = q["question"].replace(old, new)
    ctx = _jsonable(ctx)
    member_ids = [m["id"] for m in ctx["members"]]
    out = ident(ctx)
    for m, mid in zip(out["members"], member_ids):     # pseudonyms are final: never run a name through the scrubber twice
        m["id"] = mid
    for q in out["open_questions"]:
        q.pop("generated", None)
    terms = _coarse_terms(store) if coarse else {}
    if terms:
        out = _replace_terms(out, terms)
    return out


def _coarse_terms(store: MemoryStore) -> dict[str, str]:
    try:
        m = store.model("household.yaml")
    except Exception:                                    # noqa: BLE001
        return {}
    if m is None:
        return {}
    return {**{t: "[employer]" for t in m.employers}, **{t: "[place]" for t in m.places}}


def _replace_terms(obj, terms: dict[str, str]):
    if isinstance(obj, str):
        for t in sorted(terms, key=len, reverse=True):
            obj = re.sub(rf"(?<![\w]){re.escape(t)}(?![\w])", terms[t], obj, flags=re.I)
        return obj
    if isinstance(obj, dict):
        return {k: _replace_terms(v, terms) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_terms(v, terms) for v in obj]
    return obj


def _jsonable(o):
    from coach.memory.edit import jsonable
    return jsonable(o)


# ---------------------------------------------------------------- rendering and trimming

def _fmt(v):
    return "?" if v is None else (f"{v:,.0f}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))


def render_markdown(c: dict) -> str:
    L = ["# Household memory", f"_generated {c['generated']}; "
         + ("real names included (local use)" if c["names_included"] else "names replaced by pseudonyms") + "_", ""]
    if c["members"]:
        L.append("## Members")
        for m in c["members"]:
            L.append(f"- {m['id']}: {m['role']}" + (f", born {m['birth_year']}" if m.get("birth_year") else "") + (f", {m['age_band']}" if m.get("age_band") else "")
                     + (f" ({m['name']})" if m.get("name") else ""))
        L.append("")
    if c["accounts"]:
        L.append("## Accounts (from the bank data)")
        for a in c["accounts"]:
            L.append(f"- {a['bank']}: owner {a['owner'] or '?'}, purpose {a['purpose'] or '?'}, {a['source']}, "
                     f"{a['transactions']} tx ({a['first'] or '-'} to {a['last'] or '-'})")
        L.append("")
    if c["liabilities"]:
        L.append("## Liabilities")
        for x in c["liabilities"]:
            L.append(f"- {x['id']} ({x['kind']}, {x['lender'] or '?'}): {_fmt(x['monthly_payment'])} EUR/month, outstanding "
                     f"{_fmt(x['outstanding'])} as of {x['outstanding_as_of'] or '?'}, ends {x['end_date'] or '?'}, "
                     f"rate {_fmt(x['rate_nominal'])}%" + (f". {x['notes']}" if x.get("notes") else ""))
        L.append("")
    if c["assets"]:
        t = c["manual_totals"]
        L.append(f"## Assets not synced by the bank (known total {_fmt(t['assets']['total'])} EUR; "
                 f"{len(t['assets']['unknown_value'])} without a value)")
        for x in c["assets"]:
            L.append(f"- {x['id']} ({x['kind']}{', ' + x['provider'] if x.get('provider') else ''}): "
                     f"{_fmt(x['value'])} EUR as of {x['as_of'] or '?'}, {x['liquidity'] or 'liquidity ?'}"
                     + (f". {x['notes']}" if x.get("notes") else ""))
        L.append("")
    if c["contracts"]:
        L.append("## Contracts")
        for x in c["contracts"]:
            L.append(f"- {x['id']} ({x['kind'] or '?'}, {x['provider'] or '?'}): {_fmt(x['amount'])} EUR {x['period'] or ''}, "
                     f"renewal {x['renewal'] or '?'}, keep {x['keep'] if x['keep'] is not None else '?'}")
        L.append("")
    if c["events"]:
        L.append("## Events")
        for e in c["events"]:
            L.append(f"### {e['id']}" + (f" ({e['start']} to {e['end']})" if e.get("start") else ""))
            L.append(e["text"])
        L.append("")
    if c["preferences"]:
        L += ["## Preferences", c["preferences"], ""]
    if c["profile"]:
        L += ["## Profile", c["profile"], ""]
    if c["open_questions"]:
        L.append("## Open questions (what the coach does not know yet)")
        for q in c["open_questions"]:
            L.append(f"- [{q['id']}] {q['question']}" + (f" (stake {_fmt(q['stake'])} EUR)" if q.get("stake") else ""))
        L.append("")
    if c["annotations"]:
        L.append("## Categorization annotations (explanations of past transactions)")
        for a in c["annotations"]:
            L.append(f"- {a['id']} -> {a['category'] or '-'} {a['tags'] or ''}" + (f": {a['note']}" if a.get("note") else ""))
    return "\n".join(L).rstrip() + "\n"


def fit(c: dict, max_tokens: int) -> tuple[dict, str]:
    """Shrink the context until its Markdown fits `max_tokens` (estimate: 4 characters per token): least useful
    parts first. Returns (context, markdown)."""
    c = copy.deepcopy(c)
    md = render_markdown(c)
    steps = [
        lambda: c.update(annotations=[]),
        lambda: c.update(profile=c["profile"][:1500]),
        lambda: [e.update(text=e["text"][:250]) for e in c["events"]],
        lambda: c.update(open_questions=c["open_questions"][:8]),
        lambda: c.update(profile=""),
        lambda: [x.update(notes=None) for x in c["liabilities"] + c["assets"]],
        lambda: c.update(events=[{**e, "text": ""} for e in c["events"]]),
        lambda: c.update(open_questions=c["open_questions"][:3]),
        lambda: c.update(preferences=c["preferences"][:800]),
        lambda: c.update(accounts=[]),
    ]
    for s in steps:
        if est_tokens(md) <= max_tokens:
            break
        s()
        md = render_markdown(c)
    if est_tokens(md) > max_tokens:
        md = md[: max_tokens * 4].rsplit("\n", 1)[0] + "\n[... truncated to fit the token budget]\n"
    c["truncated_to_tokens"] = max_tokens if est_tokens(render_markdown(c)) > max_tokens or md.endswith("budget]\n") else None
    return c, md
