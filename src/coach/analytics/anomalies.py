"""Unusual spending (E4-5): deterministic, robust statistics, every finding carries its evidence.

Four detectors (thresholds in ``[analytics]``, see :mod:`coach.analytics.settings`):

``category_spike``   a closed month whose spending in a category is far above that category's OWN history.
    Months used: those fully covered by every account carrying the category (coverage model); at least
    `anomaly_min_history_months` (6) earlier covered months are needed. Robust z-score on the monthly totals
    (zero months count): z = (x - median) / scale with scale = max(1.4826 * MAD, 10 % of the median, 10 EUR).
    Reported when z >= `anomaly_z` (3.5) AND x - median >= `anomaly_min_excess` (50 EUR) AND x >= `anomaly_min_ratio` (1.5) x median.
    One-off / capital / exclude_from_averages transactions are left out (the user already explained them).
    Severity: high if z >= 8 or excess >= 500 EUR; medium if z >= 5 or excess >= 200 EUR; else low.
``duplicate_charge``   the same merchant (entity) and the same amount, at least `anomaly_duplicate_min_amount`,
    booked on the same account within `anomaly_duplicate_days` days. Pairs inside one recurring series and split
    transactions are ignored. Severity: high >= 100 EUR duplicated, low < 20 EUR, else medium.
``new_merchant``   a merchant (entity) never seen before whose first booking is within the last
    `anomaly_new_merchant_days` days with a payment >= `anomaly_new_merchant_min`. Severity: high >= 3 x the minimum.
``large_transaction``   inside the lookback window, a payment of a category far above the category's usual single
    payments: >= max(Q3 + `anomaly_large_tx_iqr_factor` x IQR, 3 x median, `anomaly_large_tx_min`) with at least
    8 payments of history. Recurring payments are ignored. Severity by the ratio to the threshold.
Detection windows: category spikes look at the last `anomaly_months` closed months, the other three at the last
`anomaly_lookback_days` days. Anomalies have stable ids and can be dismissed (stored in the ``anomalies`` table).
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from coach.analytics.common import (CoverageInfo, Result, Scope, median_c, money_str)
from coach.analytics.common import non_eur_note, note
from coach.analytics.coverage import last_n
from coach.analytics.dataset import CAPITAL_TAG, NO_AVERAGE_TAGS, Dataset, Tx, is_spending
from coach.analytics.recurring import RecurringResult, detect_recurring

SEVERITIES = ("high", "medium", "low")


@dataclass
class Anomaly(Result):
    id: str
    type: str                      # category_spike | duplicate_charge | new_merchant | large_transaction
    severity: str                  # high | medium | low
    subject: str                   # category or merchant entity
    period: str                    # month (category_spike) or date
    amount_c: int                  # money involved (positive)
    baseline_c: Optional[int]      # what is usual (median month / median payment / minimum), None if n/a
    score: Optional[float]         # robust z-score or ratio, None if n/a
    message: str
    evidence: list = field(default_factory=list)       # transaction keys
    accounts: list = field(default_factory=list)       # account labels
    dismissed: bool = False
    first_seen: Optional[str] = None


@dataclass
class AnomaliesResult(Result):
    as_of: date
    anomalies: list                # [Anomaly], severity then newest first
    counts: dict                   # type -> n, severity -> n
    dismissed: int
    scope: dict
    coverage: CoverageInfo
    evidence: list = field(default_factory=list)


def _id(*parts) -> str:
    return "anm_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def _eur(c: int) -> str:
    return f"{money_str(c)} EUR"


def _mad(values: list[int], med: int) -> float:
    return statistics.median(abs(v - med) for v in values)


def _run_rate_spend(ds: Dataset, scope: Optional[Scope]) -> list[Tx]:
    return [t for t in ds.txs_in(scope) if is_spending(t.category) and not t.is_saved and not t.is_one_off]


# ---------------------------------------------------------------- detectors

def category_spikes(ds: Dataset, scope: Optional[Scope] = None) -> list[Anomaly]:
    s = ds.settings
    out: list[Anomaly] = []
    by_cat: dict[str, list[Tx]] = {}
    for t in _run_rate_spend(ds, scope):
        by_cat.setdefault(t.category, []).append(t)
    for cat, txs in sorted(by_cat.items()):
        carriers = ds.carrying_accounts(cat, scope)
        months = ds.coverage.common_months(carriers)
        if len(months) < s.anomaly_min_history_months + 1:
            continue
        per: dict[str, int] = {m: 0 for m in months}
        for t in txs:
            if t.month in per:
                per[t.month] -= t.amount_c
        for m in last_n(months, s.anomaly_months):
            hist = [per[h] for h in months if h < m][-24:]
            if len(hist) < s.anomaly_min_history_months:
                continue
            med = median_c(hist)
            scale = max(1.4826 * _mad(hist, med), 0.10 * abs(med), 1000)
            x = per[m]
            z = (x - med) / scale
            excess = x - med
            if z < s.anomaly_z or excess < s.anomaly_min_excess * 100 or x < s.anomaly_min_ratio * med:
                continue
            if med == 0:
                # an intermittent category (no spending in most months): the z-score is meaningless; only a big
                # amount is worth a mention, and never as 'high' (single big payments are caught by the other detectors)
                if excess < 4 * s.anomaly_min_excess * 100:
                    continue
                sev, zr = ("medium" if excess >= 50000 else "low"), None
            else:
                sev = "high" if z >= 8 or excess >= 50000 else "medium" if z >= 5 or excess >= 20000 else "low"
                zr = round(z, 2)
            big = sorted((t for t in txs if t.month == m), key=lambda t: (t.amount_c, t.key))[:5]
            out.append(Anomaly(_id("category_spike", cat, m), "category_spike", sev, cat, m, x, med, zr,
                               f"Spending in {cat} in {m} is {_eur(x)}, versus a typical {_eur(med)} per month "
                               + (f"(robust z-score {z:.1f}, {len(hist)} earlier months)." if zr is not None else
                                  f"(usually nothing in this category; {len(hist)} earlier months)."),
                               [t.key for t in big], sorted({ds.label(a) for a in carriers})))
    return out


def duplicate_charges(ds: Dataset, scope: Optional[Scope], recurring: RecurringResult) -> list[Anomaly]:
    s = ds.settings
    since = ds.today - timedelta(days=s.anomaly_lookback_days)
    in_series = {o.tx_key: x.id for x in recurring.series for o in x.occurrences}
    groups: dict[tuple, list[Tx]] = {}
    allowed = set(ds.uids_in(scope))
    for t in ds.whole:
        if (t.amount_c < 0 and t.account in allowed and t.source != "split" and is_spending(t.category)
                and -t.amount_c >= s.anomaly_duplicate_min_amount * 100):
            groups.setdefault((t.account, t.entity or t.mkey, t.amount_c), []).append(t)
    out = []
    for (acc, ent, amt), txs in sorted(groups.items()):
        txs.sort(key=lambda t: (t.date, t.key))
        i = 0
        while i < len(txs):
            cluster = [txs[i]]
            j = i + 1
            while j < len(txs) and (txs[j].date - cluster[-1].date).days <= s.anomaly_duplicate_days:
                cluster.append(txs[j])
                j += 1
            i = j if len(cluster) > 1 else i + 1
            if len(cluster) < 2 or cluster[-1].date < since:
                continue
            ids = {in_series.get(t.key) for t in cluster}
            if None not in ids and len(ids) == 1:
                continue                                  # all inside one recurring series
            extra = (len(cluster) - 1) * -amt
            sev = "high" if extra >= 10000 else "low" if extra < 2000 else "medium"
            out.append(Anomaly(_id("duplicate_charge", acc, cluster[0].key), "duplicate_charge", sev, ent,
                               cluster[-1].date.isoformat(), extra, -amt, None,
                               f"{len(cluster)} payments of {_eur(-amt)} to {ent} within "
                               f"{(cluster[-1].date - cluster[0].date).days} day(s) "
                               f"({cluster[0].date} to {cluster[-1].date}).",
                               [t.key for t in cluster], [ds.label(acc)]))
    return out


def new_merchants(ds: Dataset, scope: Optional[Scope]) -> list[Anomaly]:
    s = ds.settings
    since = ds.today - timedelta(days=s.anomaly_new_merchant_days)
    allowed = set(ds.uids_in(scope))
    first: dict[str, date] = {}
    first_key: dict[str, str] = {}
    for t in ds.whole:                                    # all accounts: a merchant known elsewhere is not new
        key = t.entity or t.mkey
        if key not in first or t.date < first[key]:
            first[key] = t.date
            first_key[key] = t.key
    big: dict[str, list[Tx]] = {}
    for t in ds.whole:
        key = t.entity or t.mkey
        if (first[key] >= since and t.date >= since and t.amount_c < 0 and t.account in allowed
                and is_spending(t.category) and -t.amount_c >= s.anomaly_new_merchant_min * 100
                and not t.is_saved and not t.tags & (NO_AVERAGE_TAGS | {CAPITAL_TAG})):
            big.setdefault(key, []).append(t)
    out = []
    minimum = int(s.anomaly_new_merchant_min * 100)
    for key, txs in sorted(big.items()):
        top = max(-t.amount_c for t in txs)
        sev = "high" if top >= 3 * minimum else "medium"
        out.append(Anomaly(_id("new_merchant", key, first_key[key]), "new_merchant", sev, key, first[key].isoformat(), top,
                           minimum, None,
                           f"New merchant {key} (first seen {first[key]}) with a payment of {_eur(top)}.",
                           [t.key for t in sorted(txs, key=lambda t: (t.amount_c, t.key))],
                           sorted({ds.label(t.account) for t in txs})))
    return out


def large_transactions(ds: Dataset, scope: Optional[Scope], recurring: RecurringResult) -> list[Anomaly]:
    s = ds.settings
    since = ds.today - timedelta(days=s.anomaly_lookback_days)
    in_series = {o.tx_key for x in recurring.series for o in x.occurrences}
    allowed = set(ds.uids_in(scope))
    by_cat: dict[str, list[Tx]] = {}
    for t in ds.whole:
        if t.amount_c < 0 and t.account in allowed and is_spending(t.category) and not t.is_saved and not t.is_one_off:
            by_cat.setdefault(t.category, []).append(t)
    out = []
    for cat, txs in sorted(by_cat.items()):
        if len(txs) < 8:
            continue
        amts = [-t.amount_c for t in txs]
        q1, _q2, q3 = statistics.quantiles(amts, n=4, method="inclusive")
        med = median_c(amts)
        thr = max(q3 + s.anomaly_large_tx_iqr_factor * (q3 - q1), 3 * med, s.anomaly_large_tx_min * 100)
        for t in txs:
            if t.date >= since and -t.amount_c >= thr and t.key not in in_series:
                ratio = -t.amount_c / thr
                sev = "high" if ratio >= 3 else "medium" if ratio >= 1.5 else "low"
                out.append(Anomaly(_id("large_transaction", t.key), "large_transaction", sev, cat, t.date.isoformat(),
                                   -t.amount_c, med, round(ratio, 2),
                                   f"A payment of {_eur(-t.amount_c)} to {t.entity} on {t.date} is far above the usual "
                                   f"{cat} payments (median {_eur(med)}, threshold {_eur(int(thr))}).",
                                   [t.key], [ds.label(t.account)]))
    return out


# ---------------------------------------------------------------- entry points

def detect_anomalies(ds: Dataset, scope: Optional[Scope] = None, recurring: Optional[RecurringResult] = None,
                     dismissed: frozenset = frozenset(), include_dismissed: bool = False) -> AnomaliesResult:
    rec = recurring or detect_recurring(ds, scope)
    new = new_merchants(ds, scope)
    seen = {k for a in new for k in a.evidence}
    large = [a for a in large_transactions(ds, scope, rec) if a.evidence[0] not in seen]   # a new merchant says it already
    found = category_spikes(ds, scope) + duplicate_charges(ds, scope, rec) + new + large
    for a in found:
        a.dismissed = a.id in dismissed
    n_dismissed = sum(1 for a in found if a.dismissed)
    if not include_dismissed:
        found = [a for a in found if not a.dismissed]
    found.sort(key=lambda a: a.id)
    found.sort(key=lambda a: a.period, reverse=True)                      # newest first inside a severity (stable sorts)
    found.sort(key=lambda a: SEVERITIES.index(a.severity))
    counts: dict = {}
    for a in found:
        counts[a.type] = counts.get(a.type, 0) + 1
        counts["severity_" + a.severity] = counts.get("severity_" + a.severity, 0) + 1
    uids = ds.uids_in(scope)
    mh = ds.settings.anomaly_min_history_months
    notes = [note("coverage.spikeHistory", "category spikes: months covered by every account carrying the category, >= "
                  f"{mh} earlier months required", count=int(mh))]
    if ds.foreign:
        notes.append(non_eur_note(len(ds.foreign)))
    cov = ds.coverage.info(uids, [], "see module doc: per-detector windows and minimum history", notes)
    return AnomaliesResult(ds.today, found, counts, n_dismissed, (scope or Scope()).describe(), cov,
                           sorted({k for a in found for k in a.evidence}))


# ---------------------------------------------------------------- persistence

@dataclass
class AnomalyRefresh(Result):
    found: int
    new: int
    open: int
    dismissed: int
    new_ids: list = field(default_factory=list)


def refresh_anomalies(con, ds: Dataset, recurring: Optional[RecurringResult] = None,
                      now_iso: Optional[str] = None) -> AnomalyRefresh:
    """Store the current anomalies. Rows keep their `first_seen` and any dismissal; anomalies that are no longer
    detected disappear unless they were dismissed (a dismissal is a decision of the user and is kept)."""
    from coach.db import now_iso as _now
    stamp = now_iso or _now()
    old = {r[0]: (r[1], r[2]) for r in con.execute("SELECT id, first_seen, dismissed_at FROM anomalies")}
    res = detect_anomalies(ds, recurring=recurring, dismissed=frozenset(i for i, (_, dm) in old.items() if dm),
                           include_dismissed=True)
    new_ids = []
    try:
        for a in res.anomalies:
            first = old[a.id][0] if a.id in old else stamp
            a.first_seen = first
            payload = json.dumps(a.to_dict(), sort_keys=True, ensure_ascii=False)
            if a.id not in old:
                new_ids.append(a.id)
                con.execute("INSERT INTO anomalies(id, type, severity, subject, period, amount_c, payload, first_seen,"
                            " last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                            (a.id, a.type, a.severity, a.subject, a.period, a.amount_c, payload, first, stamp))
            else:
                cur = con.execute("SELECT payload FROM anomalies WHERE id=?", (a.id,)).fetchone()[0]
                if cur != payload:
                    con.execute("UPDATE anomalies SET type=?, severity=?, subject=?, period=?, amount_c=?, payload=?,"
                                " last_seen=? WHERE id=?",
                                (a.type, a.severity, a.subject, a.period, a.amount_c, payload, stamp, a.id))
        keep = {a.id for a in res.anomalies}
        for gid, (_f, dm) in old.items():
            if gid not in keep and not dm:
                con.execute("DELETE FROM anomalies WHERE id=?", (gid,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    n_open = sum(1 for a in res.anomalies if not a.dismissed)
    return AnomalyRefresh(len(res.anomalies), len(new_ids), n_open, res.dismissed, new_ids)


def dismiss_anomaly(con, anomaly_id: str, note: Optional[str] = None, now_iso: Optional[str] = None,
                    ds: Optional[Dataset] = None) -> bool:
    """Mark an anomaly as dismissed (it stays dismissed across refreshes). With `ds`, an anomaly that is currently
    detected but not stored yet is stored first; False if the id is unknown."""
    from coach.db import now_iso as _now
    if ds is not None and not con.execute("SELECT 1 FROM anomalies WHERE id=?", (anomaly_id,)).fetchone():
        found = next((a for a in detect_anomalies(ds).anomalies if a.id == anomaly_id), None)
        if found is not None:
            stamp = now_iso or _now()
            found.first_seen = stamp
            con.execute("INSERT INTO anomalies(id, type, severity, subject, period, amount_c, payload, first_seen, last_seen)"
                        " VALUES (?,?,?,?,?,?,?,?,?)", (found.id, found.type, found.severity, found.subject, found.period,
                        found.amount_c, json.dumps(found.to_dict(), sort_keys=True, ensure_ascii=False), stamp, stamp))
    cur = con.execute("UPDATE anomalies SET dismissed_at=?, dismiss_note=? WHERE id=?",
                      (now_iso or _now(), note, anomaly_id))
    con.commit()
    return cur.rowcount > 0


def undismiss_anomaly(con, anomaly_id: str) -> bool:
    cur = con.execute("UPDATE anomalies SET dismissed_at=NULL, dismiss_note=NULL WHERE id=?", (anomaly_id,))
    con.commit()
    return cur.rowcount > 0


def stored_anomalies(con, include_dismissed: bool = False) -> list[dict]:
    q = "SELECT payload, dismissed_at, dismiss_note FROM anomalies" + ("" if include_dismissed else
                                                                         " WHERE dismissed_at IS NULL")
    rows = []
    for payload, dm, note in con.execute(q + " ORDER BY id"):
        p = json.loads(payload)
        p["dismissed"] = dm is not None
        if dm:
            p["dismissed_at"], p["dismiss_note"] = dm, note
        rows.append(p)
    rows.sort(key=lambda p: (SEVERITIES.index(p["severity"]), p["id"]))
    return rows
