"""Recurring payment detection (E4-3): deterministic, no LLM, no randomness.

Algorithm
    1. Take one row per bank transaction (splits merged), skip zero amounts and ``income.refund``.
    2. Group by (direction, key, account). The key is the SEPA creditor id (+ mandate) when the parser found one
       (``tx_parse_meta``), else the canonical merchant entity, else the normalised merchant key.
    3. For a group, test the cadence on the whole group first. The median gap between consecutive bookings picks the
       candidate cadence; the cadence is accepted when >= 60 % of the gaps are within its tolerance and >= 80 % are
       within it or are a 2x / 3x multiple of it (a skipped occurrence):

           cadence     nominal   accepted gap (days)        per year
           weekly         7        5 - 9                       52.18
           biweekly      14       11 - 17                      26.09
           monthly       30       25 - 35   (+-5 days)         12
           bimonthly     61       53 - 68   (utilities)         6
           quarterly     91       81 - 101                      4
           semiannual   183      167 - 197                      2
           yearly       365      350 - 380  (+-15 days)         1

    4. Amounts (absolute) are then checked against the tolerance (default +-15 % of the median):
       ``fixed``    >= 80 % of the occurrences within the tolerance of the median amount;
       ``fixed`` too when the amounts are a few STEPS (price changes: runs of similar amounts, see E4-4);
       ``variable`` when the category is one where bills vary (energy, water, telecom...: setting
       `recurring_variable_categories`), then only the cadence matters.
    5. A group whose amounts do not fit (e.g. several subscriptions of one merchant) is split into amount clusters
       (greedy, +-tolerance) and each cluster is tested again as a fixed series.
    6. Needs >= 3 occurrences (`recurring_min_occurrences`); a yearly cadence is accepted with 2, with an explicit
       low confidence (<= 0.5).
    7. next_expected = last occurrence + one cadence step (calendar months, day clamped). A series is ``ended`` when the
       last occurrence is older than 1.5x the cadence (`recurring_ended_factor`); an active series whose next date is
       already past is ``overdue`` (the forecast puts it on the next day).
Stable id = sha1 of direction | key | account | cadence | rank of the amount cluster. Refreshing the table twice gives
identical rows (timestamps change only with the content).
"""
from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from coach.analytics.common import (CoverageInfo, Result, Scope, add_months, median_c, mul_cents)
from coach.analytics.common import non_eur_note
from coach.analytics.dataset import Dataset, Tx, is_income, is_transfer

# name -> (nominal days, (min gap, max gap), step in months or days, occurrences per year)
CADENCES = {
    "weekly": (7, (5, 9), ("d", 7), 365.25 / 7),
    "biweekly": (14, (11, 17), ("d", 14), 365.25 / 14),
    "monthly": (30, (25, 35), ("m", 1), 12.0),
    "bimonthly": (61, (53, 68), ("m", 2), 6.0),
    "quarterly": (91, (81, 101), ("m", 3), 4.0),
    "semiannual": (183, (167, 197), ("m", 6), 2.0),
    "yearly": (365, (350, 380), ("m", 12), 1.0),
}
CADENCE_ORDER = list(CADENCES)
# categories whose recurring payments are normally backed by a contract (feed for E8: contracts to document)
CONTRACT_PREFIXES = ("subscriptions.", "insurance.")
CONTRACT_CATEGORIES = {"housing.energy", "housing.water", "housing.home_insurance", "transport.car_insurance",
                       "health.health_insurance", "transport.car_loan_lease", "debt.personal_loan", "debt.loan_repayment",
                       "housing.mortgage", "housing.rental_property_loan"}


@dataclass
class Occurrence(Result):
    tx_key: str
    date: date
    amount_c: int


@dataclass
class SeriesLink(Result):
    kind: str                       # contract | liability
    id: str
    share: float                    # fraction of the occurrences matched by its merchant_match / payment_match


@dataclass
class RecurringSeries(Result):
    id: str
    direction: str                  # out | in
    kind: str                       # expense | income | transfer | saving | inflow
    key: str
    entity: str
    account: str
    account_label: str
    category: str
    cadence: str
    amount_mode: str                # fixed | variable
    n_occurrences: int
    first_date: date
    last_date: date
    next_expected: Optional[date]
    expected_amount_c: int          # signed like the bank: negative = money out
    amount_low_c: int               # signed, the less extreme of the last 6 occurrences (closest to 0)
    amount_high_c: int              # signed, the most extreme of the last 6
    yearly_cost_c: int              # positive magnitude: |expected| x occurrences per year
    status: str                     # active | ended
    overdue_days: int
    confidence: float
    confidence_label: str           # low | medium | high
    links: list = field(default_factory=list)       # [SeriesLink]
    contract_candidate: bool = False                # looks like a contract / subscription ...
    missing_contract: bool = False                  # ... and no contract / liability of the memory matches it
    tags: list = field(default_factory=list)
    price_steps: int = 0
    day_of_month: Optional[int] = None              # median day of the month of the payments (monthly-type cadences)
    end_of_month: bool = False                      # the payments are made on the last day of the month
    aliases: list = field(default_factory=list)     # other payer names accepted as occurrences (a renamed payer)
    occurrences: list = field(default_factory=list)  # [Occurrence], oldest first
    evidence: list = field(default_factory=list)


@dataclass
class RecurringResult(Result):
    as_of: date
    series: list                    # [RecurringSeries], active first then by yearly cost
    by_cadence: dict                # cadence -> count of ACTIVE series
    counts: dict
    scope: dict
    coverage: CoverageInfo
    evidence: list = field(default_factory=list)    # series ids


# ---------------------------------------------------------------- cadence / amounts

def _intervals(dates: list[date]) -> list[int]:
    return [(b - a).days for a, b in zip(dates, dates[1:])]


def detect_cadence(dates: list[date], allow_two: bool = True) -> Optional[tuple[str, float]]:
    """-> (cadence, fit) with fit = share of the gaps that match exactly, or None."""
    gaps = _intervals(sorted(dates))
    if not gaps:
        return None
    med = statistics.median(gaps)
    cand = next((c for c in CADENCE_ORDER if CADENCES[c][1][0] <= med <= CADENCES[c][1][1]), None)
    if cand is None:
        return None
    lo, hi = CADENCES[cand][1]
    centre, tol = (lo + hi) / 2, (hi - lo) / 2
    fit1 = sum(1 for g in gaps if lo <= g <= hi)
    gap_ok = sum(1 for g in gaps if lo <= g <= hi or any(abs(g - k * centre) <= tol * k for k in (2, 3)))
    n = len(gaps)
    if fit1 / n < 0.6 or gap_ok / n < 0.8:
        return None
    return cand, fit1 / n


def _amount_runs(amts: list[int], tol: float) -> list[list[int]]:
    runs: list[list[int]] = []
    for a in amts:
        if runs and abs(a - runs[-1][0]) <= tol * runs[-1][0]:
            runs[-1].append(a)
        else:
            runs.append([a])
    return runs


def _amount_mode(amts: list[int], tol: float, variable_ok: bool) -> Optional[tuple[str, float, int]]:
    """amts: absolute cents in date order -> (mode, share within tolerance, price steps) or None when they do not fit."""
    med = median_c(amts)
    share = sum(1 for a in amts if abs(a - med) <= tol * med) / len(amts)
    if share >= 0.8:
        return "fixed", share, len(_amount_runs(amts, tol)) - 1
    runs = _amount_runs(amts, tol)
    n = len(amts)
    if len(runs) <= max(2, n // 3) and all(len(r) >= 2 for r in runs[:-1]) and len(runs) >= 2:
        return "fixed", sum(len(r) for r in runs if len(r) >= 2) / n, len(runs) - 1
    if variable_ok and max(amts) <= 8 * max(1, min(amts)):
        return "variable", share, 0
    return None


def _clusters(items: list[Tx], tol: float) -> list[list[Tx]]:
    out: list[list[Tx]] = []
    for t in sorted(items, key=lambda t: (abs(t.amount_c), t.date, t.key)):
        for c in out:
            if abs(abs(t.amount_c) - abs(c[0].amount_c)) <= tol * abs(c[0].amount_c):
                c.append(t)
                break
        else:
            out.append([t])
    return [sorted(c, key=lambda t: (t.date, t.key)) for c in out]


def _confidence(fit: float, n: int, share: float, cadence: str, mode: str) -> float:
    conf = 0.5 * fit + 0.3 * min(1.0, n / 6) + 0.2 * (share if mode == "fixed" else min(share, 0.7))
    if cadence in ("yearly", "quarterly") and n < 3:
        conf = min(conf, 0.5)
    return round(min(conf, 1.0), 2)


def _label(conf: float) -> str:
    return "low" if conf < 0.6 else "medium" if conf < 0.8 else "high"


def roll_business_day(d: date) -> date:
    """Banks book a weekend date on the next Monday."""
    return d + timedelta(days=2 if d.weekday() == 5 else 1 if d.weekday() == 6 else 0)


def _month_date(base: date, months: int, dom: Optional[int], eom: bool) -> date:
    """The date `months` calendar months after `base`: the usual day of the month (not the day the previous, possibly
    clamped, payment happened to fall on), the last day of the month for end-of-month payers."""
    i = base.year * 12 + base.month - 1 + months
    y, m = i // 12, i % 12 + 1
    last = (add_months(date(y, m, 1), 1) - timedelta(days=1)).day
    return date(y, m, last if eom else min(dom or base.day, last))


def expected_date(last: date, cadence: str, dom: Optional[int] = None, eom: bool = False, k: int = 1) -> date:
    unit, n = CADENCES[cadence][2]
    if unit == "d":
        return last + timedelta(days=n * k)
    return roll_business_day(_month_date(last, n * k, dom, eom))


def next_date(last: date, cadence: str) -> date:
    return expected_date(last, cadence)


def occurrences(x: RecurringSeries, start: date, end: date) -> list[tuple[date, bool]]:
    """Dates (with an `overdue` flag) of the series between start and end. Computed from the last payment, the usual day
    of the month and the end-of-month habit, so a short month does not make later dates stick to the 28th. A missed
    next date is returned once, placed on `start`, flagged overdue."""
    if x.status != "active" or x.next_expected is None:
        return []
    out: list[tuple[date, bool]] = []
    for k in range(1, 400):
        dd = expected_date(x.last_date, x.cadence, x.day_of_month, x.end_of_month, k)
        if dd > end:
            break
        if dd >= start:
            out.append((dd, False))
        elif k == 1:
            out.append((start, True))
    return out


_REF = re.compile(r"^(?=.*\d)[A-Za-z0-9_\-]{5,}$")


def norm_key(text: str) -> str:
    """A grouping key without per-payment references: 'AMAZON PRIME*9W8 ...' -> 'AMAZON PRIME'; tokens that mix
    letters and digits (card references such as P30FCBC3) are dropped."""
    base = (text or "").split("*")[0]
    toks = [t for t in base.split() if not _REF.match(t)]
    return " ".join(toks) or (text or "")


def _group_key(ds: Dataset, t: Tx) -> str:
    meta = ds.parse_meta.get(t.key)
    if meta and meta[0]:
        return f"sepa:{meta[0]}" + (f"/{meta[1]}" if meta[1] else "")
    return norm_key(t.entity or t.mkey or t.key)


def _kind(direction: str, category: str, tags: set) -> str:
    if tags & {"savings", "investment"}:
        return "saving"
    if is_transfer(category):
        return "transfer"
    if direction == "in":
        return "income" if is_income(category) else "inflow"
    return "expense"


# ---------------------------------------------------------------- detection

def tx_matches(pat, amount_match, t) -> bool:
    """One transaction against a contract / loan pattern: the (compiled) regex on the merchant key, description or entity and, when there is an
    ``amount_match``, an amount within its band (E8: contracts that share one bank label). Shared by the series links (:func:`match_share`)
    and the loan payment linking of E9-2 (:mod:`coach.loans.payments`), so a loan is linked to its payments exactly like a contract."""
    if not (pat.search(t.mkey or "") or pat.search(t.desc or "") or pat.search(t.entity or "")):
        return False
    if amount_match is not None:
        a = abs(t.amount_c) / 100
        if abs(a - amount_match.amount) > amount_match.amount * amount_match.tolerance_pct / 100 + 0.005:
            return False
    return True


def match_share(rx: str, amount_match, txs: list) -> float:
    """Share of ``txs`` a contract / loan pattern matches (:func:`tx_matches`)."""
    try:
        pat = re.compile(rx, re.I)
    except re.error:
        return 0.0
    hit = sum(1 for t in txs if tx_matches(pat, amount_match, t))
    return hit / len(txs) if txs else 0.0


def _links(ds: Dataset, txs: list[Tx]) -> list[SeriesLink]:
    out = []
    for kind, items, attr in (("contract", ds.memory.contracts, "merchant_match"),
                              ("liability", ds.memory.liabilities, "payment_match")):
        for _rel, m in items:
            rx = getattr(m, attr, None)
            if not rx:
                continue
            share = match_share(rx, getattr(m, "amount_match", None), txs)
            if share >= 0.5:
                out.append(SeriesLink(kind, m.id, round(share, 2)))
    return sorted(out, key=lambda x: (x.kind, x.id))


def levels(amts: list[int], pct: float, min_abs_c: int) -> list[tuple[int, int, int, bool]]:
    """Price steps of a sequence of absolute amounts: [(index, old level, new level, confirmed)]. An amount that differs
    from the current level by >= pct % and >= min_abs_c is a step when the next amount stays near it (confirmed) or when it
    is the last one (unconfirmed); an odd amount followed by a return to the old level is a blip and is ignored."""
    out: list[tuple[int, int, int, bool]] = []
    if not amts:
        return out
    level = amts[0]
    for i in range(1, len(amts)):
        a = amts[i]
        if abs(a - level) >= max(level * pct / 100, min_abs_c):
            nxt = amts[i + 1] if i + 1 < len(amts) else None
            if nxt is None:
                out.append((i, level, a, False))
                level = a
            elif abs(nxt - a) < abs(nxt - level):
                out.append((i, level, a, True))
                level = a
    return out


def _day_habits(txs: list[Tx]) -> tuple[int, bool]:
    last6 = txs[-6:]
    eom = sum(1 for t in last6 if t.date == (add_months(t.date.replace(day=1), 1) - timedelta(days=1))) * 2 > len(last6)
    return int(statistics.median(t.date.day for t in last6)), eom


def _build(ds: Dataset, direction: str, key: str, account: str, txs: list[Tx], cadence: str, mode: str, fit: float,
           share: float, steps: int, aliases: Optional[list] = None) -> RecurringSeries:
    s = ds.settings
    # stable id: the first payment of the series (same when newer payments arrive, whatever the amounts do later)
    sid = "rec_" + hashlib.sha1(f"{direction}|{account}|{txs[0].key}".encode()).hexdigest()[:10]
    amts = [t.amount_c for t in txs]
    if mode == "fixed":
        # the LATEST price level: after a price step the older payments do not describe what will be debited
        st = levels([abs(a) for a in amts], s.price_change_threshold_pct, int(s.price_change_min_abs * 100))
        since = st[-1][0] if st else 0
        expected = median_c(amts[since:][-3:])
    else:
        expected = median_c(amts[-6:])
    recent = amts[-6:]
    low, high = sorted(recent, key=abs)[0], sorted(recent, key=abs)[-1]
    first, last = txs[0].date, txs[-1].date
    nominal = CADENCES[cadence][0]
    ended = (ds.today - last).days > s.recurring_ended_factor * nominal
    dom, eom = _day_habits(txs) if cadence in ("monthly", "bimonthly", "quarterly") else (None, False)
    nxt = None if ended else expected_date(last, cadence, dom, eom)
    overdue = 0 if ended or nxt is None else max(0, (ds.today - nxt).days)
    cat = Counter(t.category for t in txs).most_common()
    top = max(c for _, c in cat)
    category = next(t.category for t in reversed(txs) if dict(cat)[t.category] == top)
    tags = sorted(set().union(*[t.tags for t in txs]))
    conf = _confidence(fit, len(txs), share, cadence, mode)
    links = _links(ds, txs)
    kind = _kind(direction, category, set(tags))
    candidate = kind == "expense" and (category.startswith(CONTRACT_PREFIXES) or category in CONTRACT_CATEGORIES)
    yearly = mul_cents(abs(expected), CADENCES[cadence][3])
    return RecurringSeries(
        sid, direction, kind, key, txs[-1].entity, account, ds.label(account), category, cadence, mode, len(txs),
        first, last, nxt, expected, low, high, yearly, "ended" if ended else "active", overdue, conf, _label(conf),
        links, candidate, candidate and not links and not ended, tags, steps, dom, eom, list(aliases or []),
        [Occurrence(t.key, t.date, t.amount_c) for t in txs], [t.key for t in txs[-50:]])


def _in_list(cat: str, names: tuple) -> bool:
    return any(cat == n or (n.endswith(".") and cat.startswith(n)) for n in names)


def _lanes(txs: list[Tx]) -> list[list[Tx]]:
    """Several payments on the same day (two insurance premiums, two instalments) are separate series: the k-th largest
    payment of each day belongs to lane k."""
    by_day: dict = {}
    for t in txs:
        by_day.setdefault(t.date, []).append(t)
    if sum(1 for v in by_day.values() if len(v) > 1) < 2:
        return []
    lanes: list[list[Tx]] = []
    for day in sorted(by_day):
        for k, t in enumerate(sorted(by_day[day], key=lambda t: (abs(t.amount_c), t.key), reverse=True)):
            while len(lanes) <= k:
                lanes.append([])
            lanes[k].append(t)
    return [sorted(l, key=lambda t: (t.date, t.key)) for l in lanes if len(l) >= 2]


def _dom_lanes(txs: list[Tx], gap: int = 3) -> list[list[Tx]]:
    """Two lines of similar size billed on different days of the month ('the 6th' and 'the 20th'): split by day of month."""
    ordered = sorted(txs, key=lambda t: (t.date.day, t.date, t.key))
    lanes: list[list[Tx]] = []
    for t in ordered:
        if lanes and t.date.day - lanes[-1][-1].date.day <= gap:
            lanes[-1].append(t)
        else:
            lanes.append([t])
    return [sorted(l, key=lambda t: (t.date, t.key)) for l in lanes if len(l) >= 2] if len(lanes) > 1 else []


def _detect_group(ds: Dataset, direction: str, key: str, account: str, txs: list[Tx]) -> list[RecurringSeries]:
    s = ds.settings
    tol = s.recurring_amount_tolerance
    major = Counter(t.category for t in txs).most_common(1)[0][0]
    discretionary = _in_list(major, s.recurring_discretionary_groups) or major.split(".")[0] in s.recurring_discretionary_groups
    pair_ok = major.split(".")[0] in s.recurring_yearly_pair_groups
    minocc = max(s.recurring_min_occurrences, s.recurring_discretionary_min_occurrences) if discretionary \
        else s.recurring_min_occurrences

    def accept(n: int, cadence: str) -> bool:
        return n >= minocc or (cadence in ("yearly", "quarterly") and n >= 2 and pair_ok and not discretionary)

    def amount_mode(c: list[Tx], fit: float, allow_variable: bool):
        var_ok = allow_variable and not discretionary and (
            _in_list(major, s.recurring_variable_categories)
            or (fit >= 0.85 and len(c) >= 5 and _in_list(major, s.recurring_loose_amount_categories)))
        am = _amount_mode([abs(t.amount_c) for t in c], tol, var_ok)
        return am if am and not (discretionary and am[0] != "fixed") else None

    def try_one(c: list[Tx], allow_variable: bool) -> Optional[RecurringSeries]:
        cad = detect_cadence([t.date for t in c])
        if not cad or not accept(len(c), cad[0]):
            return None
        am = amount_mode(c, cad[1], allow_variable)
        return _build(ds, direction, key, account, c, cad[0], am[0], cad[1], am[1], am[2]) if am else None

    if len(txs) < 2:
        return []
    got = try_one(txs, True)
    if got:
        return [got]
    for t_ in (tol, 0.03):                                    # amount clusters, then a tight retry
        out = []
        for c in _clusters(txs, t_):
            if len(c) < 2:
                continue
            x = try_one(c, False)
            if x:
                out.append(x)
                continue
            # several payments on the same days INSIDE one amount cluster (two premiums of similar size): lanes
            lanes = [y for y in (try_one(l_, False) for l_ in _lanes(c)) if y]
            out += lanes or [y for y in (try_one(l_, False) for l_ in _dom_lanes(c)) if y]
        if out:
            return out
    return [y for y in (try_one(l_, True) for l_ in _lanes(txs)) if y]       # last resort: lanes over the whole group


def _txs_of(ds: Dataset, x: RecurringSeries, by_key: dict) -> list[Tx]:
    return [by_key[o.tx_key] for o in x.occurrences]


def _merge_successors(ds: Dataset, series: list[RecurringSeries], by_key: dict) -> list[RecurringSeries]:
    """A merchant whose bank descriptor varies (same service, different text: 'X PARIS' / 'X AMSTERDAM') gives several
    series that together are ONE regular payment: same account / direction / cadence / category and similar amount,
    and the union of their dates still fits the cadence (>= 80 % of the gaps) - two different services billed on
    different days of the month would not. They are joined (the newest descriptor names the series). The cleaner
    fix is to group the variants as one merchant entity (`coach merchants group`)."""
    tol = ds.settings.recurring_amount_tolerance
    out: list[RecurringSeries] = []
    pool = sorted(series, key=lambda x: (x.direction, x.account, x.cadence, x.category, x.first_date, x.id))
    i = 0
    while i < len(pool):
        a = pool[i]
        merged = False
        for j in range(i + 1, len(pool)):
            b = pool[j]
            if (b.direction, b.account, b.cadence, b.category) != (a.direction, a.account, a.cadence, a.category):
                break
            nominal = CADENCES[a.cadence][0]
            if (abs(abs(a.expected_amount_c) - abs(b.expected_amount_c)) <= tol * abs(a.expected_amount_c)
                    and a.entity != b.entity and a.amount_mode == b.amount_mode
                    and a.first_date <= b.last_date and b.first_date <= a.last_date + timedelta(days=nominal * 1.5)):
                txs = sorted(_txs_of(ds, a, by_key) + _txs_of(ds, b, by_key), key=lambda t: (t.date, t.key))
                cad = detect_cadence([t.date for t in txs])
                am = _amount_mode([abs(t.amount_c) for t in txs], tol, a.amount_mode == "variable")
                if cad and cad[0] == a.cadence and cad[1] >= 0.8 and am:
                    nb = _build(ds, a.direction, b.key, a.account, txs, cad[0], am[0], cad[1], am[1], am[2],
                                sorted(set(a.aliases + b.aliases + [a.entity])))
                    pool[j] = nb
                    merged = True
                    break
        if not merged:
            out.append(a)
        i += 1
    return out


def _absorb_renamed(ds: Dataset, series: list[RecurringSeries], by_key: dict) -> list[RecurringSeries]:
    """A payer that changes its name (a new property manager, a new bank descriptor) leaves the old series overdue while
    the money arrives under another name. A payment of another payer on the same account and direction, of a similar
    amount, in the same category group, that falls inside the cadence window after the last occurrence IS the missed
    occurrence: it joins the series and its payer name is recorded in `aliases`."""
    tol = ds.settings.recurring_amount_tolerance
    used = {o.tx_key for x in series for o in x.occurrences}
    out = []
    for x in series:
        if x.status != "active" or x.next_expected is None or x.next_expected >= ds.today:
            out.append(x)
            continue
        lo, hi = CADENCES[x.cadence][1]
        ref = abs(x.expected_amount_c)
        cand = [t for t in ds.whole if t.key not in used and t.account == x.account
                and (t.amount_c < 0) == (x.expected_amount_c < 0) and x.last_date < t.date <= ds.today
                and lo <= (t.date - x.last_date).days <= hi + 5 and abs(abs(t.amount_c) - ref) <= tol * ref
                and t.category.split(".")[0] == x.category.split(".")[0]]
        if not cand:
            out.append(x)
            continue
        t = min(cand, key=lambda t: (t.date, t.key))
        txs = _txs_of(ds, x, by_key) + [t]
        cad = detect_cadence([u.date for u in txs])
        am = _amount_mode([abs(u.amount_c) for u in txs], tol, x.amount_mode == "variable")
        if cad and cad[0] == x.cadence and am:
            used.add(t.key)
            out.append(_build(ds, x.direction, x.key, x.account, txs, cad[0], am[0], cad[1], am[1], am[2],
                              sorted(set(x.aliases + [t.entity]) - {x.entity})))
        else:
            out.append(x)
    return out


def carry_ids(ds: Dataset, series: list[RecurringSeries]) -> None:
    """Id continuity, applied by EVERY detection (so the calendar, ICS uids, price-change ids and the forecast all use the
    same id): a series keeps the id of the stored series it shares most payments with (at least half of the smaller one);
    the id derived from its first payment is used when nothing stored matches. Deterministic."""
    stored = ds.stored_members
    if not stored:
        return
    taken: set = set()
    for x in sorted(series, key=lambda x: x.id):
        if x.id in stored:                       # same first payment: same series
            taken.add(x.id)
    for x in sorted(series, key=lambda x: x.id):
        if x.id in stored:
            continue
        mine = {o.tx_key for o in x.occurrences}
        best = max((i for i in sorted(stored) if i not in taken), key=lambda i: (len(mine & stored[i]), i), default=None)
        if best and len(mine & stored[best]) * 2 >= min(len(mine), len(stored[best])):
            x.id = best
            taken.add(best)


def detect_recurring(ds: Dataset, scope: Optional[Scope] = None, include_ended: bool = True) -> RecurringResult:
    """All recurring series of the dataset (income, expenses, internal transfers, savings plans)."""
    groups: dict[tuple, list[Tx]] = {}
    allowed = set(ds.uids_in(scope))
    for t in ds.whole:
        if t.amount_c == 0 or t.category == "income.refund" or t.account not in allowed:
            continue
        groups.setdefault(("out" if t.amount_c < 0 else "in", _group_key(ds, t), t.account), []).append(t)
    series: list[RecurringSeries] = []
    by_brand: dict = {}
    for (direction, key, account), txs in sorted(groups.items()):
        if len(txs) >= 2:
            got = _detect_group(ds, direction, key, account, sorted(txs, key=lambda t: (t.date, t.key)))
            series += got
            brand = key.split()[0].lower() if key.split() and not key.startswith("sepa:") else None
            if brand and len(brand) >= 4:
                by_brand.setdefault((direction, account, brand), []).append((key, txs, got))
    # descriptor variants of one brand that INTERLEAVE ('Spotify P3A.. Goteborg' / 'Spotify France LYON'): each alone has
    # holes (or finds only a stretch), together they are regular. The pooled detection replaces the parts' series when it
    # explains more payments than they did.
    for (direction, account, brand), parts in sorted(by_brand.items()):
        if len(parts) < 2:
            continue
        pool = sorted([t for _k, ts, _g in parts for t in ts], key=lambda t: (t.date, t.key))
        key = max(parts, key=lambda kv: len(kv[1]))[0]
        pooled = _detect_group(ds, direction, key, account, pool)
        old = [x for _k, _t, g in parts for x in g]
        if pooled and sum(p.n_occurrences for p in pooled) > sum(x.n_occurrences for x in old):
            series = [x for x in series if not any(x is o for o in old)] + pooled
    by_key = {t.key: t for t in ds.whole}
    series = _absorb_renamed(ds, _merge_successors(ds, series, by_key), by_key)
    carry_ids(ds, series)
    if not include_ended:
        series = [x for x in series if x.status == "active"]
    series.sort(key=lambda x: (x.status != "active", -x.yearly_cost_c, x.id))
    active = [x for x in series if x.status == "active"]
    by_cadence = {c: sum(1 for x in active if x.cadence == c) for c in CADENCE_ORDER if any(x.cadence == c for x in active)}
    counts = {"active": len(active), "ended": len(series) - len(active),
              "missing_contract": sum(1 for x in active if x.missing_contract)}
    uids = sorted({x.account for x in series})
    notes = []
    if ds.foreign:
        notes.append(non_eur_note(len(ds.foreign)))
    cov = ds.coverage.info(uids, [], "history of each series; a series needs >= "
                           f"{ds.settings.recurring_min_occurrences} occurrences (2 for yearly, low confidence)", notes)
    return RecurringResult(ds.today, series, by_cadence, counts, (scope or Scope()).describe(), cov,
                           [x.id for x in series])


def missing_contracts(res: RecurringResult) -> list[RecurringSeries]:
    """Active expense series that look like a contract / subscription but match no memory contract or liability
    (the feed of E8: contracts to document), biggest yearly cost first."""
    return sorted((x for x in res.series if x.missing_contract and x.status == "active"),
                  key=lambda x: (-x.yearly_cost_c, x.id))


# ---------------------------------------------------------------- persistence (table recurring_series)

@dataclass
class RefreshSummary(Result):
    series: int
    created: int
    updated: int
    removed: int
    unchanged: int


def _payload(x: RecurringSeries) -> str:
    d_ = x.to_dict()
    return json.dumps(d_, sort_keys=True, ensure_ascii=False)


def refresh_recurring(con, ds: Dataset, now_iso: Optional[str] = None) -> RefreshSummary:
    """Store the detection result: upsert by stable id, delete series that are gone. Idempotent: a second call with
    the same input changes nothing (not even `updated_at`)."""
    from coach.db import now_iso as _now
    stamp = now_iso or _now()
    members: dict = {}
    for sid_, tk in con.execute("SELECT series_id, tx_key FROM recurring_members"):
        members.setdefault(sid_, set()).add(tk)
    ds.stored_members = members                      # id continuity is applied inside the detection
    res = detect_recurring(ds)
    old = {r[0]: (r[1], r[2]) for r in con.execute("SELECT id, payload, first_detected_at FROM recurring_series")}
    created = updated = unchanged = 0
    gone: list = []
    try:
        for x in res.series:
            payload = _payload(x)
            if x.id in old and old[x.id][0] == payload:
                unchanged += 1
                continue
            first = old[x.id][1] if x.id in old else stamp
            con.execute(
                "INSERT OR REPLACE INTO recurring_series(id, direction, kind, group_key, account_uid, entity, category,"
                " cadence, amount_mode, n_occurrences, first_date, last_date, next_expected, expected_amount_c,"
                " amount_low_c, amount_high_c, yearly_cost_c, status, confidence, payload, first_detected_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (x.id, x.direction, x.kind, x.key, x.account, x.entity, x.category, x.cadence, x.amount_mode,
                 x.n_occurrences, x.first_date.isoformat(), x.last_date.isoformat(),
                 x.next_expected.isoformat() if x.next_expected else None, x.expected_amount_c, x.amount_low_c,
                 x.amount_high_c, x.yearly_cost_c, x.status, x.confidence, payload, first, stamp))
            con.execute("DELETE FROM recurring_members WHERE series_id=?", (x.id,))
            con.executemany("INSERT OR IGNORE INTO recurring_members(series_id, tx_key, date, amount_c) VALUES (?,?,?,?)",
                            [(x.id, o.tx_key, o.date.isoformat(), o.amount_c) for o in x.occurrences])
            if x.id in old:
                updated += 1
            else:
                created += 1
        gone = sorted(set(old) - {x.id for x in res.series})
        for gid in gone:
            con.execute("DELETE FROM recurring_series WHERE id=?", (gid,))
            con.execute("DELETE FROM recurring_members WHERE series_id=?", (gid,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return RefreshSummary(len(res.series), created, updated, len(gone), unchanged)


def stored_series(con, include_ended: bool = True, today: Optional[date] = None, ended_factor: float = 1.5) -> list[dict]:
    """The series saved by the last refresh (their `to_dict()` payloads), active first then by yearly cost. With `today`
    the status and the overdue days are recomputed at read time (they depend on the date, not on new data)."""
    out = []
    for (p,) in con.execute("SELECT payload FROM recurring_series ORDER BY yearly_cost_c DESC, id"):
        d_ = json.loads(p)
        if today is not None:
            last = date.fromisoformat(d_["last_date"])
            ended = (today - last).days > ended_factor * CADENCES[d_["cadence"]][0]
            d_["status"] = "ended" if ended else "active"
            nxt = d_.get("next_expected")
            d_["overdue_days"] = 0 if ended or not nxt else max(0, (today - date.fromisoformat(nxt)).days)
            if ended:
                d_["next_expected"] = None
        if include_ended or d_["status"] == "active":
            out.append(d_)
    out.sort(key=lambda d_: d_["status"] != "active")
    return out
