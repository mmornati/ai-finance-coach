"""Automatic question generation (E3-3): questions derived from the data and from the memory itself.

Every candidate carries a stable ``key`` (``merchant:<key>``, ``large:<tx_key>``, ``recurring:<key>``, ``fill:...``).
Generation never repeats a key that already has a question in ANY status, so answered and dismissed questions are
not asked again, and running it twice adds nothing. Questions only contain amounts, counts, dates and business
names; transfers and merchant keys that may be natural persons are never turned into a question of their own
(they are summarised in one aggregated question without names).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from coach.memory import schemas
from coach.memory.store import MemoryStore

CONTRACT_GROUPS = {"housing", "subscriptions", "insurance", "debt"}
CONTRACT_LEAVES = {"transport.car_loan_lease", "transport.car_insurance", "health.health_insurance"}


def qid(kind: str, key: str) -> str:
    return f"q-{kind}-{hashlib.sha1(key.encode()).hexdigest()[:10]}"


def months_ago(today: dt.date, n: int) -> dt.date:
    y, m = divmod(today.year * 12 + today.month - 1 - n, 12)
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m]
    return dt.date(y, m + 1, min(today.day, last))


def _mk(kind, key, topic, question, evidence, target=None, stake=None, today=None, context=None):
    return schemas.Question(id=qid(kind, key), topic=topic, question=question, evidence=evidence, key=key,
                            suggested_target=target, created=today, origin="generated", stake=stake, context=context)


def _person_like(key, raw, types, family, first, known, allow):
    """The classifier's own guard plus a stricter one for question generation. A merchant is only NAMED in a question
    when it clearly is not a person:
      * payment-processor / P2P prefixes (PAYPAL *X, LYDIA X, SUMUP X) are looked through: what follows is judged;
      * a given name without any organisation word is a person whatever the length ("SURNAME NATHALIE LOCALITY");
      * behind a processor prefix, anything without an organisation word is treated as a person;
      * a single unknown alphabetic word that is not an organisation word and carries no label of its own is
        treated as a surname."""
    from coach.classify.candidates import LEGAL_FORMS, hold_back
    from coach.classify.parsers.common import FIRST_NAMES, ORG_WORDS, P2P_PREFIXES, PROCESSORS, strip_accents
    if hold_back(key, raw, set(types), family, first, known, allow, strict_shop=False):
        return True
    toks = [t for t in re.split(r"[^A-Z'-]+", strip_accents(key or "").upper()) if t]
    prefixed = [t for t in toks if t in PROCESSORS | P2P_PREFIXES]
    rest = {t for t in toks if t not in PROCESSORS | P2P_PREFIXES}
    org = bool(rest & (ORG_WORDS | LEGAL_FORMS))
    if rest & (FIRST_NAMES | first | family) and not org:
        return True
    if prefixed and rest and not org:
        return True
    if len(toks) == 1 and key not in known and not org and toks[0].isalpha() and len(toks[0]) >= 3:
        return True
    return False


# ---------------------------------------------------------------- generators

def merchant_questions(con, cfg, store, today, min_stake, limit):
    from coach.classify.candidates import household_names, known_merchants
    from coach.classify.commands import review_rows
    family, first = household_names(con)
    known = known_merchants(con)
    rows = review_rows(con, cfg, 0.7, 10**9)
    out, held_n, held_total = [], 0, 0.0
    for r in rows:
        if con.execute("""SELECT COUNT(*) FROM tx_enriched e WHERE e.merchant_key=? AND e.tx_key NOT IN
                          (SELECT out_tx_key FROM transfer_links UNION SELECT in_tx_key FROM transfer_links)""",
                       (r["key"],)).fetchone()[0] == 0:
            continue                                  # every transaction is a leg of a linked internal transfer
        if r["reason"] == "held_back_person_like" or _person_like(r["key"], r["raw_example"], r["types"], family, first, known,
                                                                   cfg.llm_allowlist):
            held_n += 1
            held_total += r["at_stake"]
            continue
        if r["at_stake"] < min_stake or _covered(store, r["key"], [r["raw_example"]]):
            continue                                  # below the threshold, or already described by a liability / contract
        d = con.execute("""SELECT MIN(t.booking_date), MAX(t.booking_date) FROM tx_enriched e JOIN transactions t USING(tx_key)
                           WHERE e.merchant_key=?""", (r["key"],)).fetchone()
        ev = {"payments": r["n"], "net_total": r["total"], "gross_total": r["at_stake"], "first": d[0], "last": d[1],
              "current_category": r["category"] or "none", "label_source": r["source"] or "none",
              "reason": r["reason"]}
        if r["confidence"] is not None:
            ev["confidence"] = r["confidence"]
        out.append(_mk("merchant", f"merchant:{r['key']}", "Categorization (ordered by money at stake)",
                       f"What is {r['key']}? {r['n']} payment{'s' if r['n'] != 1 else ''}, {r['total']:+,.0f} EUR between {d[0]} and {d[1]} "
                       f"(currently {r['category'] or 'not categorized'}).", ev,
                       {"file": "categorization.yaml"}, r["at_stake"], today))
    out.sort(key=lambda q: -(q.stake or 0))
    out = out[:limit]
    if held_n and held_total >= min_stake:
        out.append(_mk("people", "held-back:people", "Categorization (ordered by money at stake)",
                       f"{held_n} counterparties may be people (transfers, no business name): {held_total:,.0f} EUR "
                       "in total. They are never sent to an LLM; review them with `coach classify review` and say "
                       "what they are (family, rent, babysitter...).",
                       {"counterparties": held_n, "gross_total": round(held_total, 2)},
                       {"file": "categorization.yaml"}, held_total, today))
    return out


def large_tx_questions(con, cfg, store, today, threshold, limit):
    from coach.classify.candidates import household_names, known_merchants
    from coach.classify.rules import categorised
    family, first = household_names(con)
    known = known_merchants(con)
    counts: dict[str, int] = defaultdict(int)
    txs = list(categorised(con, memory_dir=store.root, use_splits=False))
    for t in txs:
        counts[t["key"]] += 1
    cand = [t for t in txs if abs(t["amount"]) >= threshold and t["source"] not in ("memory", "split", "override")
            and not t["category"].startswith(("transfer.", "income.")) and counts[t["key"]] <= 2]
    out = []
    for t in sorted(cand, key=lambda x: -abs(x["amount"])):
        if _person_like(t["key"], t["merchant"], {t["type"]}, family, first, known, cfg.llm_allowlist):
            continue
        ev = {"amount": t["amount"], "date": t["date"], "category": t["category"], "label_source": t["source"],
              "bank": t["bank"] or "?", "merchant": t["key"]}
        out.append(_mk("large", f"large:{t['tx_key']}", "Large one-off payments",
                       f"Is the payment of {t['amount']:+,.0f} EUR on {t['date']} to {t['key']} a one-off? "
                       f"(counted as {t['category']}; it is in the monthly averages unless tagged)", ev,
                       {"file": "categorization.yaml"}, abs(t["amount"]), today))
        if len(out) >= limit:
            break
    return out


def _covered(store, key, descs):
    import re
    pats = [m.payment_match for _, m in store.liabilities() if m.payment_match] + \
           [m.merchant_match for _, m in store.contracts() if m.merchant_match]
    for p in pats:
        try:
            if re.search(p, key, re.I) or any(re.search(p, d or "", re.I) for d in descs):
                return True
        except re.error:
            continue
    return False


def recurring_questions(con, cfg, store, today, limit):
    from coach.classify.candidates import household_names, known_merchants
    from coach.classify.rules import categorised
    family, first = household_names(con)
    known = known_merchants(con)
    groups: dict[str, list] = defaultdict(list)
    for t in categorised(con, memory_dir=store.root, use_splits=False):
        c = t["category"]
        if t["amount"] < 0 and t["key"] and (c.split(".")[0] in CONTRACT_GROUPS or c in CONTRACT_LEAVES):
            groups[t["key"]].append(t)
    out = []
    for key, txs in groups.items():
        if len(txs) < 3:
            continue
        txs.sort(key=lambda t: t["date"])
        months = {t["date"][:7] for t in txs}
        amounts = [abs(t["amount"]) for t in txs]
        mean = statistics.fmean(amounts)
        dates = [dt.date.fromisoformat(t["date"]) for t in txs]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
        if len(months) < 3 or mean < 5 or not gaps or not 20 <= statistics.median(gaps) <= 40:
            continue
        if statistics.pstdev(amounts) / mean > 0.25 or (today - dates[-1]).days > 75:
            continue
        if _covered(store, key, [t["desc"] for t in txs[-3:]]):
            continue
        if _person_like(key, txs[-1]["merchant"], {txs[-1]["type"]}, family, first, known, cfg.llm_allowlist):
            continue
        cat = txs[-1]["category"]
        out.append(_mk("recurring", f"recurring:{key}", "Recurring payments without a contract or loan file",
                       f"{key}: a monthly debit of about {mean:,.0f} EUR ({len(txs)} payments since {txs[0]['date']}, "
                       f"{cat}) has no file in liabilities/ or contracts/. Is it a loan, an insurance or a subscription? "
                       "What are its terms (start, end, renewal, notice)?",
                       {"payments": len(txs), "average": round(mean, 2), "first": txs[0]["date"], "last": txs[-1]["date"],
                        "category": cat}, {"file": "liabilities/ or contracts/"}, mean * 12, today))
    out.sort(key=lambda q: -(q.stake or 0))
    return out[:limit]


LIABILITY_KEY_FIELDS = ["lender", "start_date", "end_date", "outstanding", "outstanding_as_of", "rate.nominal"]
LEASE_KEY_FIELDS = ["lender", "start_date", "end_date", "residual_value", "mileage_limit_km", "excess_km_fee"]   # E9-6: a lease owes no capital


def key_fields(m) -> list:
    return LEASE_KEY_FIELDS if m.kind in ("loa", "lld") else LIABILITY_KEY_FIELDS


def _get(model, dotted):
    cur = model
    for p in dotted.split("."):
        cur = getattr(cur, p, None)
        if cur is None:
            return None
    return cur


def null_field_questions(store, cfg, today):
    out = []
    for rel, m in store.liabilities():
        missing = [f for f in key_fields(m) if _get(m, f) is None]
        if missing:
            out.append(_mk("fill", f"fill:liability:{m.id}", "Liabilities",
                           f"Liability {m.id} ({m.kind}): {len(missing)} key fields are empty ({', '.join(missing)}). "
                           "The loan contract or the latest statement has them.",
                           {"missing": missing, "monthly_payment": m.monthly_payment},
                           {"file": rel, "field": ",".join(missing)}, (m.monthly_payment or 0) * 12, today))
    for rel, m in store.contracts():
        missing = [f for f in ("provider", "billing.amount", "renewal", "notice_period_days") if _get(m, f) is None]
        if missing:
            out.append(_mk("fill", f"fill:contract:{m.id}", "Contracts",
                           f"Contract {m.id}: {', '.join(missing)} unknown.", {"missing": missing},
                           {"file": rel, "field": ",".join(missing)}, None, today))
    for a in store.assets():
        if a.amount is None:
            out.append(_mk("fill", f"fill:asset:{a.id}", "Assets",
                           f"Asset {a.id} ({a.kind}) has no value or balance yet: what is it today (and as of when)?",
                           {"missing": ["balance" if a.kind not in ("vehicle", "real_estate", "real_estate_rental") else "value"]},
                           {"file": "assets.yaml", "field": f"assets[{a.id}].balance"}, None, today))
    return out


def stale_questions(store, cfg, today):
    out = []
    asset_cut = months_ago(today, cfg.memory_asset_stale_months)
    liab_cut = months_ago(today, cfg.memory_stale_months)
    for a in store.assets():
        if a.amount is not None and a.as_of and a.as_of < asset_cut:
            out.append(_mk("stale", f"stale:asset:{a.id}:{a.as_of}", "Assets",
                           f"The value of asset {a.id} dates from {a.as_of}: what is it now?",
                           {"as_of": str(a.as_of), "value": a.amount}, {"file": "assets.yaml", "field": f"assets[{a.id}].balance"},
                           abs(a.amount) * 0.02, today))
    for rel, m in store.liabilities():
        if m.outstanding is not None and m.outstanding_as_of and m.outstanding_as_of < liab_cut:
            out.append(_mk("stale", f"stale:liability:{m.id}:{m.outstanding_as_of}", "Liabilities",
                           f"The outstanding capital of {m.id} dates from {m.outstanding_as_of}: what is it now?",
                           {"as_of": str(m.outstanding_as_of), "outstanding": m.outstanding},
                           {"file": rel, "field": "outstanding"}, None, today))
    return out


def odometer_questions(store, today):
    """E9-6: a leased vehicle with a mileage limit whose last odometer reading is old (or missing) within a year of the end."""
    out = []
    for rel, m in store.liabilities():
        if m.kind not in ("loa", "lld") or not m.mileage_limit_km or not m.end_date or m.end_date < today \
                or m.end_date > months_ago(today, -12):
            continue
        last = max((o.date for o in m.odometer), default=None)
        if last is not None and last >= months_ago(today, 3):
            continue
        out.append(_mk("stale", f"stale:odometer:{m.id}:{last or 'none'}", "Liabilities",
                       f"What is the odometer of the vehicle of {m.id} now? The lease ends {m.end_date} with a limit of "
                       f"{m.mileage_limit_km:,} km: a reading lets the coach project the excess-mileage cost.",
                       {"last_reading": str(last) if last else None, "end_date": str(m.end_date)},
                       {"file": rel, "field": "odometer"}, None, today))
    return out


RENTAL_PROMPT = {"account": "its bank account", "loan": "the loan that financed it", "value": "its current value", "rent_monthly": "the monthly rent",
                 "purchase_price": "the purchase price", "purchase_date": "the purchase date",
                 "commitment.start_date": "the start of the scheme commitment", "commitment.years": "the commitment length in years",
                 "commitment.rent_cap_monthly (or rent_cap_m2 and surface_m2)": "the rent cap",
                 "commitment.tenant_income_limit": "the tenant income limit", "commitment.reduction_rate_pct": "the scheme's total reduction rate"}


def rental_questions(con, store, today):
    """E15-3: one question per rental property about the facts it still lacks (never guessed: the owner states them)."""
    from coach.rental import service as rental_service
    assets = [a for a in store.assets() if a.kind == "real_estate_rental"]
    if not assets:
        return []
    liabs = [m for _r, m in store.liabilities()]
    n_acc = 0
    if con is not None:
        n_acc = con.execute("SELECT COUNT(*) FROM accounts WHERE exclude=0 AND purpose='rental'").fetchone()[0]
    n_free = sum(1 for a in assets if not a.account)
    out = []
    for a in assets:
        missing = rental_service.facts_for_questions(a, liabs, n_acc, n_free)
        if missing:
            out.append(_mk("fill", f"fill:rental:{a.id}", "Rental property",
                           f"Rental property {a.id}: to follow its cash flow, the scheme commitment and the tax figures I still need "
                           f"{', '.join(RENTAL_PROMPT.get(f, f) for f in missing)}. The deed of purchase, the lease and the loan offer have them; "
                           "`coach rental edit` records them.",
                           {"missing": missing}, {"file": "assets.yaml", "field": ",".join(f"assets[{a.id}].{m.split(' ')[0]}" for m in missing)},
                           (a.rent_monthly or 0) * 12 or None, today))
    return out


def account_questions(con, store, today):
    out = []
    for uid, bank, owner, purpose, n, first, last in con.execute("""
            SELECT a.uid, COALESCE(a.bank,''), a.owner, a.purpose, COUNT(t.tx_key), MIN(t.booking_date), MAX(t.booking_date)
            FROM accounts a LEFT JOIN transactions t ON t.account_uid=a.uid
            WHERE a.exclude=0 AND (a.owner IS NULL OR a.purpose IS NULL) GROUP BY a.uid"""):
        miss = [f for f, v in (("owner", owner), ("purpose", purpose)) if v is None]
        out.append(_mk("acct", f"acct:{uid}", "Accounts",
                       f"Account {uid[:8]} at {bank or 'an unknown bank'} ({n} transactions): "
                       f"{' and '.join(miss)} not set. Whose is it and what is it for?",
                       {"bank": bank or "?", "transactions": n, "first": first, "last": last, "missing": miss},
                       {"file": "db:accounts", "field": ",".join(miss)}, None, today))
    return out


def household_questions(con, store, today):
    out = []
    members = store.members()
    if not store.exists("household.yaml"):
        owners = [r[0] for r in con.execute("SELECT DISTINCT owner FROM accounts WHERE owner IS NOT NULL AND LOWER(owner)<>'joint'")]
        if owners:
            out.append(_mk("household", "household:init", "Household",
                           f"No household.yaml: {len(owners)} account owners exist in the data. Declare the members "
                           "(id, name, role adult/child, birth year, holder-name spellings) with `coach memory member add`.",
                           {"account_owners": len(owners)}, {"file": "household.yaml"}, None, today))
    for m in members:
        if m.birth_year is None:
            out.append(_mk("birth", f"member-birth:{m.id}", "Household",
                           f"What is the birth year of member {m.id} ({m.role})? Used for age-based analysis and goals.",
                           {"member": m.id, "role": m.role}, {"file": "household.yaml", "field": f"members[{m.id}].birth_year"},
                           None, today))
    return out


@dataclass
class GenerationResult:
    new: list[schemas.Question]
    skipped_existing: int
    by_type: dict[str, int]


def generate(store: MemoryStore, con, cfg, today: Optional[dt.date] = None, *, min_stake: Optional[float] = None,
             big_threshold: Optional[float] = None, max_merchants: int = 15, max_large: int = 10,
             max_recurring: int = 10) -> GenerationResult:
    today = today or dt.date.today()
    min_stake = cfg.memory_question_min_stake if min_stake is None else min_stake
    big = cfg.memory_big_tx_threshold if big_threshold is None else big_threshold
    cands: list[schemas.Question] = []
    cands += merchant_questions(con, cfg, store, today, min_stake, max_merchants)
    cands += large_tx_questions(con, cfg, store, today, big, max_large)
    cands += recurring_questions(con, cfg, store, today, max_recurring)
    cands += null_field_questions(store, cfg, today)
    cands += stale_questions(store, cfg, today)
    cands += odometer_questions(store, today)
    cands += rental_questions(con, store, today)
    cands += account_questions(con, store, today)
    cands += household_questions(con, store, today)
    existing_keys = {q.key for q in store.questions() if q.key}
    existing_ids = {q.id for q in store.questions()}
    # one question per subject: recurring (richest) > merchant > large one-off
    def subject(q):
        kind, _, rest = (q.key or "").partition(":")
        return q.evidence.get("merchant") if kind == "large" else rest if kind in ("merchant", "recurring") else None
    rank = {"recurring": 0, "merchant": 1, "large": 2}
    best: dict[str, int] = {}
    for q in cands:
        sj = subject(q)
        if sj:
            r = rank[q.key.split(":", 1)[0]]
            best[sj] = min(best.get(sj, 9), r)
    asked_text = "\n".join(f"{q.question}\n{q.source_text or ''}" for q in store.questions()).upper()

    def asked_in_free_text(sj):
        """A hand-written (e.g. migrated) question already names this merchant."""
        return bool(sj) and len(sj) >= 5 and re.search(rf"(?<![A-Z0-9]){re.escape(sj.upper())}(?![A-Z0-9])", asked_text) is not None
    new, skipped, seen = [], 0, set()
    for q in cands:
        sj = subject(q)
        if asked_in_free_text(sj):
            skipped += 1
            continue
        if sj and rank[q.key.split(":", 1)[0]] > best[sj]:
            continue                                           # a richer question about the same subject exists
        if q.key in existing_keys or q.id in existing_ids or q.key in seen:
            skipped += 1
            continue
        seen.add(q.key)
        new.append(q)
    new.sort(key=lambda q: -(q.stake or 0))
    by_type: dict[str, int] = defaultdict(int)
    for q in new:
        by_type[q.key.split(":", 1)[0]] += 1
    return GenerationResult(new, skipped, dict(by_type))


# ---------------------------------------------------------------- is a question already answered by the data?

def resolved_reason(q: schemas.Question, store: MemoryStore, con=None, cfg=None) -> Optional[str]:
    """For an OPEN question: why it looks already resolved by facts now present in memory (None if not)."""
    if q.status != "open":
        return None
    key = q.key or ""
    kind, _, rest = key.partition(":")
    if kind == "fill":
        what, _, ident = rest.partition(":")
        if what == "liability":
            for _, m in store.liabilities():
                if m.id == ident:
                    miss = [f for f in key_fields(m) if _get(m, f) is None]
                    return "all key fields of this liability are now filled" if not miss else None
        if what == "asset":
            for a in store.assets():
                if a.id == ident and a.amount is not None:
                    return "the asset now has a value"
        if what == "rental":
            from coach.rental import service as rental_service
            n_acc = con.execute("SELECT COUNT(*) FROM accounts WHERE exclude=0 AND purpose='rental'").fetchone()[0] if con is not None else 0
            assets = [a for a in store.assets() if a.kind == "real_estate_rental"]
            for a in assets:
                if a.id == ident and not rental_service.facts_for_questions(a, [m for _r, m in store.liabilities()], n_acc,
                                                                            sum(1 for x in assets if not x.account)):
                    return "every fact asked about this rental property is now recorded"
    elif kind == "member-birth":
        for m in store.members():
            if m.id == rest and m.birth_year is not None:
                return "birth_year is now set"
    elif kind == "household" and store.exists("household.yaml") and store.members():
        return "household.yaml now exists"
    elif kind == "merchant" and con is not None:
        row = con.execute("SELECT source FROM merchants WHERE merchant_key=?", (rest,)).fetchone()
        if row and row[0] == "user":
            return "the merchant now has your own label"
        if cfg is not None:
            from coach.classify.candidates import memory_decided_keys
            if rest in memory_decided_keys(con, store.root):
                return "every transaction of this merchant is now decided by a memory annotation"
    elif kind == "acct" and con is not None:
        row = con.execute("SELECT owner, purpose FROM accounts WHERE uid=?", (rest,)).fetchone()
        if row and row[0] is not None and row[1] is not None:
            return "the account now has an owner and a purpose"
    elif kind == "stale":
        if rest.startswith("odometer:"):
            ident = rest.split(":")[1]
            cut = months_ago(dt.date.today(), 3)
            for _, m in store.liabilities():
                if m.id == ident and any(o.date >= cut for o in m.odometer):
                    return "a recent odometer reading is now recorded"
            return None
    t = q.suggested_target
    if t and t.field and "[" not in t.field and "," not in t.field and not t.file.startswith("db:") and \
            t.file.endswith(".yaml") and store.exists(t.file):
        data = store.load_plain(t.file)
        cur = data
        for part in t.field.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur not in (None, "", []):
            return f"{t.file} field {t.field} is now set"
    return None
