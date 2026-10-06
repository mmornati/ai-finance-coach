"""Internal transfer matching (E1-12, E14-7): money moving between the household's own accounts.

A transfer is a debit on account A and a credit on account B (A != B), same currency, same absolute amount
to the cent, booking dates at most `window_days` apart, neither leg already linked. A pair is linked
automatically only when it is unambiguous both ways (the debit has exactly one candidate credit and that credit
exactly one candidate debit) AND both legs look like transfers AND neither is classified income.* AND the
confidence reaches `min_confidence`. Candidates need a known tx_type (normalised rows only). Pairs below the
threshold are PROPOSALS (`coach transfers --proposals`); ambiguous ones are listed by `--unmatched`; the user
settles both with `coach transfers link OUT_KEY IN_KEY`. Evidence: a leg looks like a transfer when its tx_type
is transfer_*/internal_transfer/wero_*/person_transfer_* or its text has a transfer word (VIR, VIREMENT...);
it is STRONG when the other account's IBAN or holder name appears in a description, or the parser recognised an
own-account transfer. Salary vs a same-amount payment, or rent paid vs rent received, are therefore not linked
by default: they only ever become proposals.

Card purchases / refunds and cash withdrawals (tx_type card, card_refund, atm) are never candidates: a card
refund of the same amount on another account is not an internal transfer.

E14-7, across the household's banks: the legs of a pair on DIFFERENT banks may be booked further apart (``cross_window_days``, default 5:
a weekend, a bank holiday, an instant credit booked on another day than the debit) and their descriptions rarely agree (each bank writes
its own text). Two more kinds of evidence therefore count as STRONG, beyond the other account's IBAN and holder name: a household MEMBER
(household.yaml: name or alias, every word) named in a leg who OWNS the account on the other side and is not the owner of this leg's own
account (a parent's debit that names the child, the child's credit that names the parent). A parent's top-up of a child's account is an
internal transfer of the household (excluded from spending and income) but the credit is still the child's income in the children's view
(:mod:`coach.household.kids`). A pair is flagged ``scope: household`` when the two accounts belong to different members.

Classification: both legs of a link resolve to ``transfer.internal`` (see classify.rules.resolve for the
precedence) and so drop out of spending and income.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from coach.db import now_iso

NOT_TRANSFER_TYPES = ("card", "card_refund", "atm")
TRANSFER_TYPES = ("transfer_in", "transfer_out", "internal_transfer", "wero_in", "wero_out",
                  "person_transfer_in", "person_transfer_out", "savings_internal", "topup", "fx_exchange")
# types the parser only assigns to money moving between the user's own accounts / pockets
OWN_TYPES = ("internal_transfer", "savings_internal", "topup", "fx_exchange")
HINT_RE = re.compile(r"\b(VIR|VIREMENT|VIRT|TRANSFER|BONIFICO|REVOLUT|LIVRET|VERSEMENT|PERMANENT|TOP[- ]?UP)\b", re.I)
DEFAULT_MIN_CONFIDENCE = 0.85
# card payments to these merchants are usually top-ups of another own account (Revolut card funded by the main
# card...): they may pair with the credit on the other account, as PROPOSALS only, never auto-linked
DEFAULT_TOPUPS = ("REVOLUT", "LYDIA", "PAYPAL")


class TransferError(Exception):
    pass


@dataclass
class Leg:
    tx_key: str
    account_uid: str
    account: str
    date: str
    amount: float
    currency: str
    description: str
    tx_type: str | None = None
    merchant_key: str | None = None
    topup: bool = False
    bank: str | None = None

    def short(self) -> str:
        return f"{self.date} {self.amount:>10.2f} {self.currency or ''} {self.account[:22]:<22} {(self.description or '')[:48]}"


@dataclass
class MatchResult:
    linked: list[dict] = field(default_factory=list)      # unambiguous AND confidence >= threshold (auto-linkable)
    proposals: list[dict] = field(default_factory=list)   # unambiguous, evidence on both legs, below threshold
    ambiguous: list[dict] = field(default_factory=list)   # {"debit": Leg, "candidates": [Leg]}


def _legs(con, topups: tuple[str, ...] = DEFAULT_TOPUPS) -> list[Leg]:
    """Unlinked, normalised (tx_type known) legs that are not card/ATM operations (except card payments to a
    top-up merchant). A row without tx_type (an import not yet normalised) is never a candidate."""
    sql = """SELECT t.tx_key, t.account_uid, COALESCE(a.label, a.name, a.uid), t.booking_date, t.amount,
                    t.currency, t.description, e.tx_type, e.merchant_key, a.bank
             FROM transactions t LEFT JOIN accounts a ON a.uid=t.account_uid
             JOIN tx_enriched e ON e.tx_key=t.tx_key
             WHERE t.amount <> 0 AND t.booking_date IS NOT NULL AND e.tx_type IS NOT NULL
               AND t.tx_key NOT IN (SELECT out_tx_key FROM transfer_links)
               AND t.tx_key NOT IN (SELECT in_tx_key FROM transfer_links)"""
    top = re.compile(r"\b(" + "|".join(re.escape(t.upper()) for t in topups) + r")\b") if topups else None
    out = []
    for r in con.execute(sql):
        leg = Leg(*r[:9], bank=r[9])
        if leg.tx_type == "card" and top and top.search((leg.merchant_key or leg.description or "").upper()):
            leg.topup = True
        elif leg.tx_type in NOT_TRANSFER_TYPES:
            continue
        out.append(leg)
    return out


def _days(a: str, b: str) -> int | None:
    try:
        return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)
    except ValueError:
        return None


def _identity(con) -> dict[str, tuple[str, set[str]]]:
    """account uid -> (normalised IBAN, holder name tokens) used to spot 'the other account' in a description.
    Holder tokens come from accounts.owner or a person-like account name, never from a product label."""
    from coach.classify.parsers.common import account_holder_tokens
    out = {}
    for uid, iban, name, owner, ctype in con.execute("SELECT uid, iban, name, owner, cash_account_type FROM accounts"):
        toks = account_holder_tokens(name, owner, ctype)
        if len(toks) < 2:      # a lone surname proves nothing about who the counterparty is (H2)
            toks = set()
        out[uid] = (re.sub(r"\s+", "", iban or "").upper(), toks)
    return out


def _member_evidence(d: Leg, c: Leg, owners: dict, people) -> bool:
    """E14-7: a household member who owns the account on the OTHER side is named in a leg (see the module doc)."""
    if people is None or not people:
        return False
    od, oc = people.resolve(owners.get(d.account_uid)), people.resolve(owners.get(c.account_uid))
    if oc in people.by_id and oc != od and people.text_mentions(d.description, oc):
        return True
    return od in people.by_id and od != oc and people.text_mentions(c.description, od)


def _strong(d: Leg, c: Leg, ident, owners: dict | None = None, people=None) -> bool:
    """Evidence that the money went to / came from THE OTHER account itself: its IBAN in a description, a DIFFERENT
    holder's name as the leading counterparty, or BOTH legs recognised by the parser as own-account transfers
    (a reciprocal match). A single internal_transfer leg is not enough, and neither is the holder's own name
    appearing somewhere in the text."""
    if d.tx_type in OWN_TYPES and c.tx_type in OWN_TYPES:
        return True
    if _member_evidence(d, c, owners or {}, people):
        return True
    from coach.classify.parsers.common import lead_tokens, strip_vir_prefix
    for leg, mine, other in ((d, d.account_uid, c.account_uid), (c, c.account_uid, d.account_uid)):
        iban, toks = ident.get(other, ("", set()))
        text = (leg.description or "").upper()
        if iban and iban in re.sub(r"\s+", "", text):
            return True
        # the OTHER account's holder as the counterparty at the START of the text, when that holder is not the
        # one of this very account (the same person's name sits in every line of their own accounts, salary
        # included: that proves nothing)
        lead = set(lead_tokens(strip_vir_prefix(text))[: len(toks) + 1])
        if toks and toks != ident.get(mine, ("", set()))[1] and toks <= lead:
            return True
    return False


def _looks_like_transfer(leg: Leg) -> bool:
    return leg.topup or leg.tx_type in TRANSFER_TYPES or bool(HINT_RE.search(leg.description or ""))


def confidence(debit: Leg, credit: Leg, days: int, strong: bool = False) -> float:
    """Strong evidence (other account's IBAN / holder name, own-transfer type): 0.98 - 0.03/day.
    Weak (both legs merely look like transfers): 0.70 - 0.05/day, so by default never auto-linked."""
    c = (0.98 - 0.03 * days) if strong else (0.70 - 0.05 * days)
    return round(min(0.99, max(0.3, c)), 2)


def opts(cfg) -> dict:
    """Keyword arguments for find_pairs / match_transfers from the configuration (+ memory annotations)."""
    from coach.classify.rules import load_annotations
    out = {"min_confidence": cfg.transfer_min_confidence, "topups": cfg.transfer_topup_merchants,
           "annotations": load_annotations(cfg.memory_dir), "cross_window_days": cfg.transfer_cross_bank_window_days}
    try:                                                  # E14-7: the members' names and aliases are evidence between banks
        from coach.household import people as people_mod
        from coach.memory.store import MemoryStore
        out["people"] = people_mod.load(MemoryStore(cfg.memory_dir, history=False))
    except Exception:                                     # noqa: BLE001 - no household: the previous behaviour
        pass
    return out


def find_pairs(con, window_days: int = 3, min_confidence: float = DEFAULT_MIN_CONFIDENCE,
               topups: tuple[str, ...] = DEFAULT_TOPUPS, annotations: list[dict] | None = None,
               cross_window_days: int | None = None, people=None) -> MatchResult:
    from coach.classify.rules import annotation_for, load_rules, resolve
    rules = load_rules()
    annotations = annotations or []
    legs = _legs(con, topups)
    ident = _identity(con)
    owners = {uid: owner for uid, owner in con.execute("SELECT uid, owner FROM accounts")}
    cross = max(window_days, cross_window_days) if cross_window_days is not None else window_days
    rejected = {(o, i) for o, i in con.execute("SELECT out_tx_key, in_tx_key FROM transfer_rejections")}
    income_cache: dict[str, bool] = {}

    def is_income(l: Leg) -> bool:     # a leg the classification calls income.* is never a transfer candidate
        if l.tx_key not in income_cache:
            cat = resolve(con, l.tx_key, l.tx_type, l.merchant_key, rules, linked=False)[0]
            # a type rule (e.g. internal_transfer) outranks the merchant label in resolve(); income wins here
            m = con.execute("SELECT category FROM merchants WHERE merchant_key=?", (l.merchant_key,)).fetchone()
            ann = annotation_for(annotations, l.tx_key, l.merchant_key, l.description, l.date, l.amount, cat)
            cats = [cat, m[0] if m else "", ann.get("category", "") if ann else ""]
            income_cache[l.tx_key] = any((x or "").startswith("income.") for x in cats)
        return income_cache[l.tx_key]

    def pair_dict(d: Leg, c: Leg, n: int, strong: bool) -> dict:
        od, oc = (people.resolve(owners.get(d.account_uid)), people.resolve(owners.get(c.account_uid))) if people else (None, None)
        p = {"debit": d, "credit": c, "days": n, "strong": strong, "topup": d.topup or c.topup,
             "confidence": confidence(d, c, n, strong), "scope": "household" if (od and oc and od != oc) else "own",
             "to_child": bool(people and oc in people.by_id and people.role(oc) == "child")}
        if p["topup"]:      # card top-ups are only ever proposed
            p["confidence"] = min(p["confidence"], 0.6)
        return p

    def candidates(pool: list[Leg], lo: int, hi_same: int, hi_cross: int):
        """(debit, credit, gap, strong) for every plausible pair of `pool` whose gap is in (lo, hi]: `hi_same` for legs on one bank, `hi_cross` for two banks."""
        credits: dict[tuple, list[Leg]] = {}
        for l in pool:
            if l.amount > 0:
                credits.setdefault((round(l.amount * 100), l.currency), []).append(l)
        for d in pool:
            if d.amount >= 0:
                continue
            for c in credits.get((round(-d.amount * 100), d.currency), []):
                if c.account_uid == d.account_uid or (d.tx_key, c.tx_key) in rejected:
                    continue
                n = _days(d.date, c.date)
                hi = hi_cross if (d.bank and c.bank and d.bank != c.bank) else hi_same
                if n is None or n > hi or n <= lo:
                    continue
                if not (_looks_like_transfer(d) and _looks_like_transfer(c)) or is_income(d) or is_income(c):
                    continue
                yield d, c, n, _strong(d, c, ident, owners, people)

    # PASS 1: the original logic inside `window_days`, whatever the banks. Unambiguous pairs are locked here and never re-opened.
    cand_out: dict[str, list[tuple[Leg, int, bool]]] = {}
    cand_in: dict[str, list[Leg]] = {}
    debits = {}
    for d, c, n, strong in candidates(legs, -1, window_days, window_days):
        debits[d.tx_key] = d
        cand_out.setdefault(d.tx_key, []).append((c, n, strong))
        cand_in.setdefault(c.tx_key, []).append(d)
    res = MatchResult()
    locked: set[str] = set()
    for dk, cs in sorted(cand_out.items(), key=lambda kv: debits[kv[0]].date):
        d = debits[dk]
        if len(cs) == 1 and len(cand_in[cs[0][0].tx_key]) == 1:
            c, n, strong = cs[0]
            p = pair_dict(d, c, n, strong)
            (res.linked if p["confidence"] >= min_confidence and not p["topup"] else res.proposals).append(p)
            locked |= {d.tx_key, c.tx_key}
        else:
            res.ambiguous.append({"debit": d, "candidates": [c for c, _, _ in cs],
                                  "competing_debits": sorted({x.tx_key for c, _, _ in cs for x in cand_in[c.tx_key]
                                                              if x.tx_key != dk})})
    # PASS 2 (E14-7): only the legs still unmatched, across DIFFERENT banks, up to `cross` days apart (banks book on other days). The gap
    # beyond `window_days` needs STRONG evidence (the other account's IBAN / holder, a household member who owns the other side, own-account
    # types); ties go to the smallest gap. A pass-1 pair is never touched, so this pass can only add links, never take one away.
    if cross > window_days:
        pool = [l for l in legs if l.tx_key not in locked]
        wide = sorted(((n, d.date, d.tx_key, c.tx_key, d, c) for d, c, n, strong in candidates(pool, window_days, window_days, cross) if strong),
                      key=lambda t: t[:4])
        used: set[str] = set()
        matched_debits: set[str] = set()
        for n, _, _, _, d, c in wide:
            if d.tx_key in used or c.tx_key in used:
                continue
            p = pair_dict(d, c, n, True)
            (res.linked if p["confidence"] >= min_confidence and not p["topup"] else res.proposals).append(p)
            used |= {d.tx_key, c.tx_key}
            matched_debits.add(d.tx_key)
        if used:      # an ambiguous pass-1 group whose debit or credits were just settled is no longer open
            res.ambiguous = [g for g in res.ambiguous if g["debit"].tx_key not in used]
    return res


def match_transfers(con, window_days: int = 3, dry_run: bool = False, auto_link: bool = True,
                    min_confidence: float = DEFAULT_MIN_CONFIDENCE, topups: tuple[str, ...] = DEFAULT_TOPUPS,
                    annotations: list[dict] | None = None, cross_window_days: int | None = None, people=None) -> MatchResult:
    """Detect pairs; with `auto_link` store those at or above `min_confidence`. Without it (the scheduled job's
    default) nothing is written: the pairs are only proposed."""
    res = find_pairs(con, window_days, min_confidence, topups, annotations, cross_window_days, people)
    if not dry_run and auto_link:
        for p in res.linked:
            con.execute("INSERT INTO transfer_links(out_tx_key, in_tx_key, amount, confidence, method, created_at) "
                        "VALUES (?,?,?,?, 'auto', ?)",
                        (p["debit"].tx_key, p["credit"].tx_key, abs(p["debit"].amount), p["confidence"], now_iso()))
        con.commit()
    return res


def resolve_tx(con, ref: str) -> str:
    if con.execute("SELECT 1 FROM transactions WHERE tx_key=?", (ref,)).fetchone():
        return ref
    esc = ref.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = con.execute("SELECT tx_key FROM transactions WHERE tx_key LIKE ? ESCAPE '\\' LIMIT 3",
                       (f"%{esc}%",)).fetchall()
    if len(rows) == 1 and len(ref) >= 6:
        return rows[0][0]
    if len(rows) > 1:
        raise TransferError(f"{ref!r} matches several transactions; give more of the key")
    raise TransferError(f"No transaction matches {ref!r}")


def link_transfer(con, out_ref: str, in_ref: str, allow_amount_mismatch: bool = False) -> int:
    out_key, in_key = resolve_tx(con, out_ref), resolve_tx(con, in_ref)
    o = con.execute("SELECT account_uid, amount, currency FROM transactions WHERE tx_key=?", (out_key,)).fetchone()
    i = con.execute("SELECT account_uid, amount, currency FROM transactions WHERE tx_key=?", (in_key,)).fetchone()
    if o[1] >= 0 or i[1] <= 0:
        raise TransferError("the first transaction must be the debit (negative) and the second the credit (positive)")
    if o[0] == i[0]:
        raise TransferError("both transactions are on the same account")
    if round(-o[1] * 100) != round(i[1] * 100) and not allow_amount_mismatch:
        raise TransferError(f"amounts differ ({o[1]} vs {i[1]}); use --allow-amount-mismatch for a transfer with "
                            "fees or a currency change")
    for key in (out_key, in_key):
        if (row := con.execute("SELECT id FROM transfer_links WHERE out_tx_key=? OR in_tx_key=?",
                               (key, key)).fetchone()):
            raise TransferError(f"{key} is already part of link #{row[0]} (unlink it first)")
    cur = con.execute("INSERT INTO transfer_links(out_tx_key, in_tx_key, amount, confidence, method, created_at) "
                      "VALUES (?,?,?,1.0,'manual',?)", (out_key, in_key, abs(o[1]), now_iso()))
    con.execute("DELETE FROM transfer_rejections WHERE out_tx_key=? AND in_tx_key=?", (out_key, in_key))
    con.commit()
    return cur.lastrowid


def unlink_transfer(con, ref: str) -> dict:
    """Remove a link by id or by either leg's tx_key; the pair is remembered so auto-matching skips it."""
    row = None
    if ref.isdigit():
        row = con.execute("SELECT id, out_tx_key, in_tx_key FROM transfer_links WHERE id=?", (int(ref),)).fetchone()
    if not row:
        row = con.execute("SELECT id, out_tx_key, in_tx_key FROM transfer_links WHERE out_tx_key=? OR in_tx_key=?",
                          (ref, ref)).fetchone()
    if not row:
        raise TransferError(f"No transfer link matches {ref!r} (see `coach transfers`)")
    con.execute("DELETE FROM transfer_links WHERE id=?", (row[0],))
    con.execute("INSERT OR REPLACE INTO transfer_rejections VALUES (?,?,?)", (row[1], row[2], now_iso()))
    con.commit()
    return {"id": row[0], "out": row[1], "in": row[2]}


def list_links(con) -> list[dict]:
    rows = con.execute("""
        SELECT l.id, l.amount, l.confidence, l.method, l.created_at,
               o.tx_key, o.booking_date, COALESCE(ao.label, ao.name, ao.uid), o.description,
               i.tx_key, i.booking_date, COALESCE(ai.label, ai.name, ai.uid), i.description, ao.owner, ai.owner
        FROM transfer_links l
        LEFT JOIN transactions o ON o.tx_key=l.out_tx_key LEFT JOIN accounts ao ON ao.uid=o.account_uid
        LEFT JOIN transactions i ON i.tx_key=l.in_tx_key LEFT JOIN accounts ai ON ai.uid=i.account_uid
        ORDER BY COALESCE(o.booking_date, ''), l.id""").fetchall()
    keys = ["id", "amount", "confidence", "method", "created_at", "out_key", "out_date", "out_account", "out_desc",
            "in_key", "in_date", "in_account", "in_desc", "out_owner", "in_owner"]
    return [dict(zip(keys, r)) for r in rows]


def format_links(links: list[dict]) -> str:
    if not links:
        return "no transfer links (run `coach transfers match`)"
    lines = [f"{'id':>4} {'amount':>10} {'conf':>5} {'how':<7} {'from':<24} -> {'to':<24} dates"]
    for l in links:
        lines.append(f"{l['id']:>4} {l['amount']:>10.2f} {l['confidence']:>5.2f} {l['method']:<7} "
                     f"{(l['out_account'] or '?')[:24]:<24} -> {(l['in_account'] or '?')[:24]:<24} "
                     f"{l['out_date']} / {l['in_date']}")
    return "\n".join(lines)


def format_proposals(res: MatchResult) -> str:
    items = [("auto-linkable", p) for p in res.linked] + [("proposal", p) for p in res.proposals]
    if not items:
        return "no transfer proposals"
    lines = [f"{len(items)} candidate pair(s) not linked yet; confirm with `coach transfers link OUT_KEY IN_KEY` "
             "(or `coach transfers match` for the auto-linkable ones):"]
    for kind, p in items:
        scope = ", household transfer" + (" to a child" if p.get("to_child") else "") if p.get("scope") == "household" else ""
        lines.append(f"\n  {p['confidence']:.2f} {kind} ({'strong' if p['strong'] else 'weak'} evidence{', card top-up' if p['topup'] else ''}{scope}, {p['days']}d apart)"
                     f"\n  OUT {p['debit'].short()}\n      key {p['debit'].tx_key}"
                     f"\n  IN  {p['credit'].short()}\n      key {p['credit'].tx_key}")
    return "\n".join(lines)


def format_ambiguous(res: MatchResult) -> str:
    if not res.ambiguous:
        return "no ambiguous transfer candidates"
    lines = [f"{len(res.ambiguous)} debit(s) with several (or contested) candidate credits; "
             "pick with `coach transfers link OUT_KEY IN_KEY`:"]
    for g in res.ambiguous:
        lines.append(f"\n  OUT {g['debit'].short()}\n      key {g['debit'].tx_key}")
        for c in g["candidates"]:
            lines.append(f"    IN  {c.short()}\n      key {c.tx_key}")
        if g["competing_debits"]:
            lines.append(f"      (also claimed by {len(g['competing_debits'])} other debit(s))")
    return "\n".join(lines)


def linked_keys(con) -> set[str]:
    out = set()
    for a, b in con.execute("SELECT out_tx_key, in_tx_key FROM transfer_links"):
        out.add(a)
        out.add(b)
    return out
