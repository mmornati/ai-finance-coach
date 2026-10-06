"""The input of every analytics function: one immutable-ish :class:`Dataset` loaded once per call.

``load_dataset(con, memory_dir, today=...)`` reads the categorised transactions (category, source, tags, event,
entity, splits: see :mod:`coach.classify.rules`), the accounts (owner / purpose / exclude), the latest balances, the
sync log, the consents and a read-only snapshot of the memory (liabilities, contracts, assets, budgets, goals,
members). The analytics functions are pure functions of that object plus explicit parameters, which is what the
finance MCP server (E6-1) will call: build a Dataset, call a function, return ``result.to_dict()``.

Rules applied here, once, for everybody
    * Accounts flagged ``exclude`` / ``needs_review`` are out (same as the classifier).
    * Only EUR transactions are analysed. A transaction in another currency is left out of every total and listed in
      ``Dataset.foreign`` (each result mentions the count in its coverage notes): no conversion is attempted.
    * Spending = every category except ``transfer.*`` and ``income.*``. Positive amounts in a spending category
      (card refunds, reimbursements) reduce spending. ``income.refund`` is also a negative spending (see cashflow).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from coach.analytics.common import Scope, add_months_key, d, last_closed_month, month_key, to_cents
from coach.analytics.coverage import CoverageModel
from coach.analytics.settings import AnalyticsSettings

SAVED_TAGS = frozenset({"savings", "investment"})
NO_AVERAGE_TAGS = frozenset({"one_off", "exclude_from_averages"})
CAPITAL_TAG = "capital"
BALANCE_PREFERENCE = ("CLBD", "ITBD", "XPCD", "CLAV", "ITAV", "OPBD", "OPAV", "FWAV")


def is_transfer(cat: str) -> bool:
    return cat.startswith("transfer.")


def is_income(cat: str) -> bool:
    """Real income. ``income.refund`` is NOT income: it is a refund (negative spending)."""
    return cat.startswith("income.") and cat != "income.refund"


def is_spending(cat: str) -> bool:
    return not cat.startswith(("transfer.", "income."))


@dataclass(frozen=True)
class AccountInfo:
    uid: str
    label: str
    bank: Optional[str]
    owner: Optional[str]
    purpose: Optional[str]
    currency: Optional[str] = None


@dataclass(frozen=True)
class Tx:
    key: str
    date: dt.date
    amount_c: int
    category: str
    source: str
    tags: frozenset
    event: Optional[str]
    entity: str
    merchant: Optional[str]
    mkey: str
    account: str
    type: Optional[str]
    desc: str
    split_index: Optional[int] = None
    split_of_c: Optional[int] = None
    person: Optional[str] = None            # E14-3: the household member it belongs to, 'joint', or None (see coach.household.attribution)

    @property
    def month(self) -> str:
        return month_key(self.date)

    @property
    def is_saved(self) -> bool:
        return bool(self.tags & SAVED_TAGS)

    @property
    def is_one_off(self) -> bool:
        """Excluded from averages (one_off / exclude_from_averages) or capital: not part of the run rate."""
        return bool(self.tags & NO_AVERAGE_TAGS) or CAPITAL_TAG in self.tags


@dataclass(frozen=True)
class Balance:
    account: str
    amount_c: int
    type: str
    fetched_at: str
    as_of: dt.date


@dataclass
class MemorySnapshot:
    liabilities: list = field(default_factory=list)       # [(file, Liability)]
    contracts: list = field(default_factory=list)         # [(file, Contract)]
    assets: list = field(default_factory=list)
    budgets: list = field(default_factory=list)
    goals: list = field(default_factory=list)
    members: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    budget_problems: list = field(default_factory=list)   # invalid entries of budgets.yaml ('id: why'), valid ones still used
    goal_problems: list = field(default_factory=list)
    country: Optional[str] = None                         # household.yaml country (FR / IT): the cancellation rules to apply
    people: Any = None                                    # E14: coach.household.people.People (members, attribution rules, kid budgets, allocations)


def load_memory(memory_dir) -> MemorySnapshot:
    """Read-only, tolerant: an invalid file contributes nothing and a warning (``coach memory check`` details it)."""
    from coach.memory.store import MemoryStore
    snap = MemorySnapshot()
    if memory_dir is None or not Path(memory_dir).exists():
        return snap
    try:
        store = MemoryStore(memory_dir, history=False)
        for i in store.validate_all():
            if i.level == "error":
                snap.warnings.append(f"{i.file}: {i.message}")
        snap.liabilities = store.liabilities()
        snap.contracts = store.contracts()
        snap.assets = store.assets()
        snap.budgets, snap.budget_problems = store.budgets_checked()
        snap.goals, snap.goal_problems = store.goals_checked()
        snap.members = store.members()
        from coach.household import people as people_mod
        snap.people = people_mod.load(store)
        try:
            c = (store.load_plain("household.yaml") if store.exists("household.yaml") else {}).get("country")
            snap.country = c.upper() if isinstance(c, str) and c.upper() in ("FR", "IT") else None
        except Exception:                                  # noqa: BLE001 - an unreadable household file: the default country
            snap.country = None
    except Exception as e:                                     # noqa: BLE001 - analytics must not die on memory
        snap.warnings.append(f"memory not readable: {type(e).__name__}: {str(e)[:100]}")
    return snap


@dataclass
class Dataset:
    today: dt.date
    settings: AnalyticsSettings
    accounts: dict                      # uid -> AccountInfo
    txs: list                           # [Tx] sorted by (date, key), splits expanded (one per part)
    coverage: CoverageModel
    memory: MemorySnapshot
    balances: dict                      # uid -> Balance (preferred type, latest)
    parse_meta: dict                    # tx_key -> (creditor_id, mandate_ref)
    consents: list                      # [Consent]
    foreign: list                       # tx keys left out because the currency is not EUR
    stored_members: dict = field(default_factory=dict)   # stored recurring series id -> its tx keys (id continuity)
    decisions: list = field(default_factory=list)        # E8-6: confirmed decisions (subs.decisions.Decision), for the reminders
    last_sync: dict = field(default_factory=dict)        # uid -> date of the last successful sync (kept for the person views)
    stats: dict = field(default_factory=dict)            # uid -> (first, last, n) booked transactions (same)
    member: Optional[str] = None                          # set on a person view (see member_view)
    owned: Optional[frozenset] = None                     # on a person view: the uids of the accounts that person OWNS (the only ones with a balance of theirs)
    _cache: dict = field(default_factory=dict, repr=False)

    # -- scope helpers
    def accounts_in(self, scope: Optional[Scope] = None) -> list:
        scope = scope or Scope()
        return [a for u, a in sorted(self.accounts.items()) if scope.accepts(a)]

    def uids_in(self, scope: Optional[Scope] = None) -> list:
        return [a.uid for a in self.accounts_in(scope)]

    # -- E14-4: one person's view
    def person_ids(self) -> list:
        """Member ids that have at least one transaction attributed (plus 'joint' when some are)."""
        return sorted({t.person for t in self.txs if t.person})

    def member_view(self, member: str) -> "Dataset":
        """The same dataset seen as ONE member: only the transactions attributed to `member` (on any account: a joint account's
        payments made with their card count), the accounts they own or spent through, and the balances of the accounts they OWN.
        Every analytics function then works on it unchanged. A joint account's balance is never a person's balance."""
        key = ("member_view", member)
        got = self._cache.get(key)
        if got is not None:
            return got
        import dataclasses as dc
        txs = [t for t in self.txs if t.person == member]
        people = self.memory.people
        owned = {u for u, a in self.accounts.items() if people is not None and people.resolve(a.owner) == member}
        keep = owned | {t.account for t in txs}
        accounts = {u: a for u, a in self.accounts.items() if u in keep}
        view = dc.replace(self, accounts=accounts, txs=txs, coverage=self.coverage.subset(accounts), member=member,
                          balances={u: b for u, b in self.balances.items() if u in owned}, owned=frozenset(owned), _cache={})
        self._cache[key] = view
        return view

    def balance_uids(self, scope: Optional[Scope] = None) -> list:
        """The accounts whose BALANCE counts for this view: all of them for the household, only the owned ones for a person's view."""
        return [u for u in self.uids_in(scope) if self.owned is None or u in self.owned]

    def resolve_account(self, ref: str) -> Optional[AccountInfo]:
        if ref in self.accounts:
            return self.accounts[ref]
        hits = [a for a in self.accounts.values() if (a.label or "").lower() == ref.lower()]
        if len(hits) == 1:
            return hits[0]
        hits = [a for a in self.accounts.values() if a.uid.startswith(ref)] if len(ref) >= 6 else []
        return hits[0] if len(hits) == 1 else None

    def txs_in(self, scope: Optional[Scope] = None) -> list:
        if scope is None or scope.is_all:
            return self.txs
        ok = set(self.uids_in(scope))
        return [t for t in self.txs if t.account in ok]

    @property
    def whole(self) -> list:
        """One Tx per bank transaction: split parts merged back (whole amount, category of the largest part)."""
        w = self._cache.get("whole")
        if w is None:
            w, seen = [], {}
            for t in self.txs:
                if t.split_index is None:
                    w.append(t)
                    continue
                seen.setdefault(t.key, []).append(t)
            for key, parts in seen.items():
                big = max(parts, key=lambda p: (abs(p.amount_c), -(p.split_index or 0)))
                w.append(Tx(key, big.date, parts[0].split_of_c if parts[0].split_of_c is not None
                            else sum(p.amount_c for p in parts), big.category, "split", big.tags, big.event,
                            big.entity, big.merchant, big.mkey, big.account, big.type, big.desc, None, None, big.person))
            w.sort(key=lambda t: (t.date, t.key))
            self._cache["whole"] = w
        return w

    def carriers_split(self, category: str, scope: Optional[Scope] = None) -> tuple[list, list]:
        """(major, minor) accounts carrying `category`. An account is MAJOR when it carries at least
        `coverage_min_share` (10 %) of the category's money over the last 12 closed months (all its history when it has
        none there); minor accounts do not narrow the month window of an average. If no account reaches the share, all of
        them are major."""
        key = ("carry", category, scope)
        got = self._cache.get(key)
        if got is None:
            allowed = set(self.uids_in(scope))
            txs = [t for t in self.txs if t.category == category and t.account in allowed]
            lo = add_months_key(last_closed_month(self.today), -11) + "-01"
            recent = [t for t in txs if t.date.isoformat() >= lo] or txs
            tot = sum(abs(t.amount_c) for t in recent)
            per: dict[str, int] = {}
            for t in recent:
                per[t.account] = per.get(t.account, 0) + abs(t.amount_c)
            every = sorted({t.account for t in txs})
            major = [u for u in every if tot and per.get(u, 0) / tot >= self.settings.coverage_min_share]
            if not major:
                major = every
            got = (major, [u for u in every if u not in major])
            self._cache[key] = got
        return got

    def carrying_accounts(self, category: str, scope: Optional[Scope] = None) -> list:
        """The major accounts carrying `category` (see :meth:`carriers_split`): the ones whose coverage sets the window."""
        return self.carriers_split(category, scope)[0]

    def flow_accounts(self, scope: Optional[Scope] = None) -> list:
        """Accounts that carry at least one income / spending / saved transaction: the ones whose coverage matters
        for income and spending figures (an account that only moves money internally, e.g. an empty savings pocket,
        cannot make a month 'incomplete')."""
        key = ("flow", scope)
        got = self._cache.get(key)
        if got is None:
            allowed = set(self.uids_in(scope))
            got = sorted({t.account for t in self.txs if t.account in allowed
                          and (t.is_saved or not is_transfer(t.category))})
            self._cache[key] = got
        return got

    def balance_of(self, uid: str) -> Optional[Balance]:
        return self.balances.get(uid)

    def label(self, uid: str) -> str:
        a = self.accounts.get(uid)
        return a.label if a else uid


def _prefer(types: list) -> Optional[str]:
    for t in BALANCE_PREFERENCE:
        if t in types:
            return t
    return sorted(types)[0] if types else None


def load_balances(con, accounts, until: Optional[dt.date] = None) -> dict:
    """Latest snapshot per account, of the most 'booked' balance type available (CLBD, then ITBD, XPCD, ...)."""
    latest: dict[str, dict[str, tuple]] = {}
    for uid, fetched, btype, amount, cur, ref in con.execute(
            "SELECT account_uid, fetched_at, balance_type, amount, currency, reference_date FROM balances "
            "ORDER BY fetched_at, rowid"):
        if uid not in accounts or amount is None or (cur not in (None, "", "EUR")):
            continue
        if until is not None and d(fetched) > until:               # an --as-of run does not see later snapshots
            continue
        latest.setdefault(uid, {})[btype or "?"] = (fetched, to_cents(amount), ref)
    out = {}
    for uid, by_type in latest.items():
        t = _prefer(list(by_type))
        fetched, cents, ref = by_type[t]
        out[uid] = Balance(uid, cents, t, fetched, d(fetched))
    return out


def load_dataset(con, memory_dir=None, *, today: Optional[dt.date] = None, settings: Optional[AnalyticsSettings] = None,
                 rules=None, annotations=None, memory: Optional[MemorySnapshot] = None) -> Dataset:
    from coach.classify.rules import categorised
    from coach.ingest import consent as consent_mod
    today = today or dt.date.today()
    settings = settings or AnalyticsSettings()
    accounts: dict[str, AccountInfo] = {}
    for uid, label, bank, owner, purpose, cur in con.execute(
            "SELECT uid, COALESCE(NULLIF(label,''), NULLIF(name,''), uid), bank, owner, purpose, currency "
            "FROM accounts WHERE exclude=0 AND needs_review=0 ORDER BY uid"):
        accounts[uid] = AccountInfo(uid, label, bank, owner, purpose, cur)
    currency = {k: c for k, c in con.execute("SELECT tx_key, currency FROM transactions")}
    memory = memory if memory is not None else load_memory(memory_dir)
    from coach.household import attribution as attribution_mod
    from coach.household.people import People
    people = memory.people if memory.people is not None else People()
    attributor = attribution_mod.attributor_for(con, people)           # E14-3: manual > memory rule > account owner
    foreign: list[str] = []
    txs: list[Tx] = []
    for t in categorised(con, rules=rules, annotations=annotations, memory_dir=memory_dir):
        if t["account"] not in accounts or d(t["date"]) > today:       # nothing after `today`: as-of runs see the past only
            continue
        if currency.get(t["tx_key"]) not in (None, "", "EUR"):
            if t["tx_key"] not in foreign:
                foreign.append(t["tx_key"])
            continue
        split = t.get("split_index")
        txs.append(Tx(t["tx_key"], d(t["date"]), to_cents(t["amount"]), t["category"], t["source"],
                      frozenset(t["tags"]), t["event"], t["entity"] or t["merchant"] or t["key"] or t["tx_key"],
                      t["merchant"], t["key"] or "", t["account"], t["type"], t["desc"] or "", split,
                      to_cents(t["split_of"]) if split is not None else None,
                      attributor.attribute(t["tx_key"], t["account"], t["desc"] or "", t["key"] or "", t["amount"]).person))
    txs.sort(key=lambda t: (t.date, t.key, t.split_index or 0))
    stats: dict[str, tuple] = {}
    for uid, lo, hi, n in con.execute("SELECT account_uid, MIN(booking_date), MAX(booking_date), COUNT(*) "
                                      "FROM transactions WHERE booking_date IS NOT NULL AND booking_date <= ? GROUP BY 1", (today.isoformat(),)):
        if uid in accounts:
            stats[uid] = (d(lo), d(hi), n)
    last_sync = {uid: d(ts) for uid, ts in con.execute(
        "SELECT account_uid, MAX(ran_at) FROM sync_log WHERE ok=1 AND substr(ran_at,1,10) <= ? GROUP BY 1",
        (today.isoformat(),)) if uid in accounts and ts}
    meta = {k: (c, m) for k, c, m in con.execute(
        "SELECT tx_key, creditor_id, mandate_ref FROM tx_parse_meta WHERE creditor_id IS NOT NULL OR mandate_ref IS NOT NULL")}
    now = dt.datetime.combine(today, dt.time(12, 0), tzinfo=dt.timezone.utc)
    consents = [c for c in consent_mod.list_consents(con, now=now)]
    try:
        stored: dict = {}
        for sid_, tk in con.execute("SELECT series_id, tx_key FROM recurring_members"):
            stored.setdefault(sid_, set()).add(tk)
    except Exception:                                      # noqa: BLE001 - table absent: no continuity to keep
        stored = {}
    try:
        from coach.subs import decisions as decisions_mod
        decisions = decisions_mod.load(con, states=("confirmed",))
    except Exception:                                      # noqa: BLE001 - table absent (migration pending): no decisions
        decisions = []
    return Dataset(today, settings, accounts, txs, CoverageModel(accounts, stats, last_sync, today),
                   memory, load_balances(con, accounts, today), meta,
                   consents, sorted(foreign), stored, decisions, last_sync, stats)
