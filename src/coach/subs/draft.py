"""Bootstrap the contract files (E8-1): a contract DRAFT for every contract-like recurring series that has no contract file.

    kind            inferred from the category group: insurance_home / insurance_car / insurance_health, energy, telecom,
                    streaming, software, membership;
    provider        the cleaned merchant / entity name;
    billing         the amount and the period of the series (monthly / bimonthly / quarterly / yearly; a weekly, biweekly or
                    semiannual payment has no matching period: billing stays null and a question asks);
    merchant_match  a regular expression from the series' bank label, checked against the series' own payments (so the file really
                    links to the series);
    start_date      the first payment seen (a LOWER BOUND of the real start: the contract may be older);
    everything else null, with an open question ``fill:contract:<id>`` naming the missing fields.

The draft is a VALUE; how it is stored depends on who asks: the CLI creates sealed proposals (``coach subs draft-contracts``) or, with
``--write``, previews and writes after a typed yes (source ``cli``); the web app writes after a preview (source ``ui``); the coach
can only create proposals (``contracts_draft``).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.common import money_str
from coach.analytics.recurring import match_share
from coach.i18n_msg import server_msg
from coach.memory import yamlio
from coach.skills.subaudit import group_of
from coach.subs.inventory import kind_of_category

PERIODS = {"monthly": "monthly", "bimonthly": "bimonthly", "quarterly": "quarterly", "yearly": "yearly"}
MISSING_FIELDS = ("renewal", "commitment_end", "notice_period_days")


@dataclass
class Draft:
    series_id: str
    contract_id: str
    rel: str
    value: dict
    kind: str
    provider: str
    missing: list = field(default_factory=list)
    yearly_c: int = 0
    warnings: list = field(default_factory=list)
    warnings_msg: list = field(default_factory=list)     # the same warnings as messages for the web app (docs/i18n.md "Server text")
    series_ids: list = field(default_factory=list)       # every series the file covers (the first is the one it was drafted from)
    shared_label: bool = False                           # another series has the same bank label: ask what this contract covers

    def to_dict(self) -> dict:
        return {"series": self.series_id, "contract_id": self.contract_id, "file": self.rel, "kind": self.kind,
                "provider": self.provider, "missing": self.missing, "yearly": money_str(self.yearly_c), "value": self.value,
                "warnings": self.warnings, "warnings_msg": self.warnings_msg, "series_ids": self.series_ids, "shared_label": self.shared_label}


def candidates(rec, ds=None) -> list:
    """Active expense series in a contract-like group with no contract file, biggest first (a series already drafted into a contract
    file whose bank label could not be matched counts as having one)."""
    from coach.subs.inventory import series_contracts
    have = series_contracts(ds, rec) if ds is not None else {x.id: True for x in rec.series if any(l.kind == "contract" for l in x.links)}
    out = [x for x in rec.series if x.kind == "expense" and x.status == "active" and group_of(x.category) is not None and x.id not in have]
    return sorted(out, key=lambda x: (-x.yearly_cost_c, x.id))


def clean_provider(entity: str) -> str:
    toks = [t for t in re.split(r"\s+", re.sub(r"[^\w&'.-]+", " ", entity or "").strip()) if t]
    toks = [t for t in toks if not (any(ch.isdigit() for ch in t) and len(t) >= 5)]
    s = " ".join(toks) or (entity or "contract").strip()
    return s.title() if s.isupper() else s


GENERIC_TOKENS = {"PRLV", "CB", "CARTE", "VIR", "PAIEMENT", "PRELEVEMENT", "PRELEV", "SEPA", "ECH", "REGLEMENT", "FACTURE", "PAYPAL", "PAGAMENTO",
                  "ADDEBITO", "BONIFICO", "DOM", "SDD", "TIP", "SQ", "SUMUP"}


def match_regex(ds, x) -> Optional[str]:
    """A regex of the series' bank label (``^`` + the most common merchant key, spaces as ``\\s+``), the longest prefix of up to four words
    that matches at least half of the series' payments the way the memory link does (the words after the first often vary: a
    reference, a town); a prefix that is one generic payment word (PRLV, VIR, ...) is never used. It must also pass the schema's
    "not too generic" check."""
    from coach.memory.schemas import _regex
    txs = {t.key: t for t in ds.txs}
    mine = [txs[o.tx_key] for o in x.occurrences if o.tx_key in txs]
    if not mine:
        return None
    cands = [c for c, _n in Counter(t.mkey for t in mine if t.mkey).most_common(2)] + [x.entity]
    for cand in cands:
        words = [t for t in re.split(r"\s+", (cand or "").strip()) if t][:4]
        for n in range(len(words), 0, -1):
            toks = words[:n]
            if n == 1 and (toks[0].upper() in GENERIC_TOKENS or len(toks[0]) < 4):
                continue
            rx = "^" + r"\s+".join(re.escape(t) for t in toks)
            try:
                _regex(rx, "merchant_match")
                pat = re.compile(rx, re.I)
            except (re.error, ValueError):
                continue
            hit = sum(1 for t in mine if pat.search(t.mkey or "") or pat.search(t.desc or "") or pat.search(t.entity or ""))
            if hit / len(mine) >= 0.5:
                return rx
    return None


def _unique_id(base: str, taken: set) -> str:
    cand, n = base, 2
    while cand in taken:
        cand = f"{base}-{n}"
        n += 1
    taken.add(cand)
    return cand


def _txs(ds, x, cache: dict) -> list:
    if "map" not in cache:
        cache["map"] = {t.key: t for t in ds.txs}
    return [cache["map"][o.tx_key] for o in x.occurrences if o.tx_key in cache["map"]]


def amount_band(x) -> dict:
    """An ``amount_match`` for one series: its expected amount, the tolerance at least 3 % and wide enough for the spread of its recent payments."""
    exp = abs(x.expected_amount_c) / 100
    spread = abs(abs(x.amount_high_c) - abs(x.amount_low_c)) / 100
    return {"amount": round(exp, 2), "tolerance_pct": max(3.0, round(spread * 100 / exp + 0.5, 1)) if exp else 3.0}


class _AM:
    def __init__(self, d):
        self.amount, self.tolerance_pct = d["amount"], d["tolerance_pct"]


def draft_for(ds, x, taken_ids: set, rx=None, peers=(), amount_match=None, shared_label=False) -> Draft:
    """The draft of one series. ``peers``: series the file covers too (indistinguishable from this one: same label and amounts); the billing
    is then left empty. ``amount_match``: a band that tells it apart from another series with the same bank label."""
    provider = clean_provider(x.entity)
    kind = kind_of_category(x.category)
    cid = _unique_id(yamlio.slug(provider, 28), taken_ids)
    period = PERIODS.get(x.cadence)
    warnings: list = []
    warnings_msg: list = []

    def warn(m: dict) -> None:
        warnings.append(m["text"])
        warnings_msg.append(m)
    billing = {"amount": None, "period": None}
    missing = list(MISSING_FIELDS)
    if peers:
        series = ", ".join(p.id for p in peers)
        warn(server_msg("subs.draft.indistinguishable",
                        f"{len(peers)} other recurring series cannot be told apart from this one by label and amount ({series}): "
                        "one contract file covers them all; give it a more specific merchant_match if they are separate contracts",
                        count=len(peers), series=series))
        missing.insert(0, "billing.amount")
    elif period:
        billing = {"amount": round(abs(x.expected_amount_c) / 100, 2), "period": period}
    else:
        warn(server_msg("subs.draft.noBillingPeriod", f"the {x.cadence} payment has no matching billing period: billing left empty",
                        cadence=x.cadence))
        missing.insert(0, "billing.amount")
    rx = rx if rx is not None else match_regex(ds, x)
    if rx is None:
        warn(server_msg("subs.draft.noMerchantMatch", "no merchant_match regex could be built that links to this series' payments"))
        missing.insert(0, "merchant_match")
    also = f"; also covers {', '.join(p.id for p in peers)}" if peers else ""
    value = {"id": cid, "provider": provider, "kind": kind, "holder": None, "merchant_match": rx, "amount_match": amount_match,
             "start_date": min([x.first_date, *[p.first_date for p in peers]]),
             "renewal": None, "billing": billing, "commitment_end": None, "notice_period_days": None, "cancellation": None,
             "usage": None, "keep": None, "contract_number": None, "documents": [],
             "notes": f"drafted from {x.id}{also}: dates and terms to fill"}
    return Draft(x.id, cid, f"contracts/{cid}.yaml", value, kind, provider, missing, x.yearly_cost_c + sum(p.yearly_cost_c for p in peers), warnings,
                 warnings_msg, [x.id, *[p.id for p in peers]], shared_label)


def drafts(ds, rec, existing_ids=None, series_ids=None, limit: Optional[int] = None) -> list[Draft]:
    """One draft per series, each tested against EVERY series' payments before it is proposed:

    * a label that is also the start of another series' label (``^ORANGE\\s+SA`` against ``ORANGE SA PARIS``) or the label of several
      policies of one insurer would link the contract to all of them: it gets an ``amount_match`` band (the series' own amount, tolerance
      >= 3 %) that tells them apart;
    * series that stay indistinguishable (same label, same amounts) are merged explicitly into one file;
    * :func:`link_report` then counts, for every series, the contracts that match it (memory and drafts): each must be exactly one."""
    taken = set(existing_ids if existing_ids is not None else {c.id for _r, c in ds.memory.contracts})
    cache: dict = {}
    cands = [x for x in candidates(rec, ds) if series_ids is None or x.id in series_ids]
    plans = [{"xs": [x], "rx": match_regex(ds, x), "am": None, "shared": False} for x in cands]
    others = [y for y in rec.series if y.kind == "expense"]

    def collides(plan) -> list:
        """The series (not covered by this plan) whose payments the plan's pattern matches (a contract links at >= half of a series' payments)."""
        if plan["rx"] is None:
            return []
        am = _AM(plan["am"]) if plan["am"] else None
        own = {x.id for x in plan["xs"]}
        return [y for y in others if y.id not in own and match_share(plan["rx"], am, _txs(ds, y, cache)) >= 0.5]
    for _ in range(len(plans) + 2):
        changed = False
        for plan in plans:
            hit = collides(plan)
            if hit and plan["am"] is None:
                plan["shared"], plan["am"], changed = True, amount_band(plan["xs"][0]), True
                hit = collides(plan)
            for y in hit:                                      # still matched with the band: indistinguishable -> merge explicitly
                other = next((q for q in plans if q is not plan and any(z.id == y.id for z in q["xs"])), None)
                if other is not None:
                    plan["xs"] += other["xs"]
                    plan["am"] = None
                    plans.remove(other)
                    changed = True
                    break
        if not changed:
            break
    out = []
    for plan in plans:
        xs = sorted(plan["xs"], key=lambda z: (-z.yearly_cost_c, z.id))
        out.append(draft_for(ds, xs[0], taken, plan["rx"], xs[1:], plan["am"], plan["shared"]))
        if limit and len(out) >= limit:
            break
    for pr in link_report(ds, rec, out):
        for d in out:
            if pr["series"] in d.series_ids:
                m = server_msg("subs.draft.matchedBy", f"{pr['series']} would be matched by {pr['matches']} contract file(s) (expected exactly one)",
                               series=pr["series"], count=pr["matches"])
                d.warnings.append(m["text"])
                d.warnings_msg.append(m)
    return out


def link_report(ds, rec, drafts_: list, existing=None) -> list:
    """Series that would NOT be matched by exactly one contract (the memory's contracts plus the drafts): ``[{series, matches}]``."""
    cache: dict = {}
    pats = [(c.id, c.merchant_match, c.amount_match) for _r, c in (existing if existing is not None else ds.memory.contracts) if c.merchant_match]
    for d in drafts_:
        if d.value.get("merchant_match"):
            am = d.value.get("amount_match")
            pats.append((d.contract_id, d.value["merchant_match"], _AM(am) if am else None))
    bad = []
    for y in rec.series:
        if y.kind != "expense" or y.status != "active":
            continue
        n = sum(1 for _cid, rx, am in pats if match_share(rx, am, _txs(ds, y, cache)) >= 0.5)
        if n != 1 and any(y.id in d.series_ids for d in drafts_):
            bad.append({"series": y.id, "matches": n})
    return bad


def by_kind(ds_drafts: list) -> dict:
    return dict(sorted(Counter(d.kind for d in ds_drafts).items()))


def ops_for(d: Draft, *, iso: bool = False) -> list:
    """The ``create`` operation of a draft (dates as ISO strings for a proposal)."""
    v = dict(d.value)
    if v.get("amount_match") is None:
        v.pop("amount_match")
    if iso and isinstance(v.get("start_date"), dt.date):
        v["start_date"] = v["start_date"].isoformat()
    return [{"op": "create", "value": v}]


def question_for(d: Draft, today: dt.date):
    """The open question that asks for the missing fields (same key as the generator's ``fill:contract:<id>``: never twice)."""
    from coach.memory.qgen import _mk
    key = f"fill:contract:{d.contract_id}"
    extra = (" Several recurring payments share this bank label: what does this contract cover (home, car, health, other), and who holds it?"
             if d.shared_label else "")
    return _mk("fill", key, "Contracts",
               f"Contract {d.contract_id} ({d.provider}) was drafted from the bank payments: {', '.join(d.missing)} unknown. "
               "The contract or the customer area has them." + extra, {"missing": d.missing, "drafted_from": d.series_id},
               {"file": d.rel, "field": ",".join(d.missing)}, round(d.yearly_c / 100, 2), today)
