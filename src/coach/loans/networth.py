"""Net worth (E9-4): bank balances + manual assets - what is owed, by category and by owner, with the unknowns listed.

Rules (stated in every result)
    * Bank accounts: the latest balance of the most booked type available (:func:`coach.analytics.dataset.load_balances`); a balance
      that is not a booked one (available / expected) is counted but flagged ``balance_type``. No balance = unknown, never zero.
    * Assets of the memory: ``balance`` / ``value`` with its ``as_of``; older than ``asset_stale_months`` = stale (still counted,
      flagged). A value that is not recorded is UNKNOWN (listed, not counted). An asset marked ``connected`` is counted through the
      balance of its bank account and not twice.
    * Liabilities: the capital still due computed from the amortization schedule when it is computable (``source: schedule``), else
      the declared ``outstanding`` (flagged stale when its date is old), else UNKNOWN. A loan is never estimated from the instalment.
    * Categories: cash (current accounts), savings (savings accounts, regulated savings), investments (employee savings, life
      insurance, securities, pension, crypto), real_estate, vehicles, other. ``net_worth`` = known assets - known liabilities;
      ``complete`` is false as soon as one item is unknown, and the figure is then "the known part".
Owners: the account's owner, the asset's ``holder`` / ``holders`` (several = ``joint``), the liability's ``holder``; else ``unassigned``.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.common import Result, add_months, money_str
from coach.loans import schedule as S

CATEGORIES = ("cash", "savings", "investments", "real_estate", "vehicles", "other")
BOOKED = ("CLBD", "ITBD", "OPBD")
ASSET_CATEGORY = {
    "regulated_savings": "savings", "savings_account": "savings", "employee_savings_plan": "investments",
    "life_insurance_savings": "investments", "securities": "investments", "pension": "investments", "crypto": "investments",
    "real_estate": "real_estate", "real_estate_rental": "real_estate", "vehicle": "vehicles", "cash": "cash",
}
GENERIC_NAME = {
    "regulated_savings": "regulated savings", "savings_account": "savings account", "employee_savings_plan": "employee savings plan",
    "life_insurance_savings": "life insurance savings", "securities": "securities", "pension": "pension", "crypto": "crypto",
    "real_estate": "house", "real_estate_rental": "rental property", "vehicle": "vehicle", "cash": "cash",
    "mortgage": "mortgage", "car_loan": "car loan", "loa": "car lease (LOA)", "lld": "car lease (LLD)",
    "consumer_loan": "consumer loan", "bnpl": "buy-now-pay-later",
}
UNASSIGNED = "unassigned"
BALANCE_STALE_DAYS = 7


def category_of_asset(kind: Optional[str]) -> str:
    return ASSET_CATEGORY.get(kind or "", "other")


@dataclass
class Component(Result):
    type: str                       # account | asset | liability
    id: str
    label: str
    category: str                   # a CATEGORIES value, or "liability"
    owner: str
    status: str                     # known | unknown | excluded (a lease: not a debt, the amount is its remaining rents)
    amount_c: Optional[int] = None  # positive magnitude (a liability: what is owed)
    as_of: Optional[dt.date] = None
    stale: bool = False
    source: Optional[str] = None    # balance | declared | schedule
    balance_type: Optional[str] = None
    note: Optional[str] = None
    kind: Optional[str] = None      # the asset / liability kind


@dataclass
class NetWorth(Result):
    as_of: dt.date
    net_worth_c: int
    assets_c: int
    liabilities_c: int
    complete: bool
    by_category_c: dict             # category -> cents (assets) ; "liabilities" -> cents owed
    by_owner: dict                  # owner -> {assets_c, liabilities_c, net_worth_c, n_unknown}
    components: list                # [Component]
    unknown: list                   # [{type, id, label, reason}]
    stale: list                     # [{type, id, label}]
    n_unknown: int = 0
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"as_of": self.as_of.isoformat(), "net_worth": money_str(self.net_worth_c), "assets": money_str(self.assets_c),
                "liabilities": money_str(self.liabilities_c), "complete": self.complete, "n_unknown": self.n_unknown,
                "by_category": {k: money_str(v) for k, v in self.by_category_c.items()},
                "by_owner": {o: {"assets": money_str(v["assets_c"]), "liabilities": money_str(v["liabilities_c"]),
                                 "net_worth": money_str(v["net_worth_c"]), "n_unknown": v["n_unknown"]} for o, v in self.by_owner.items()},
                "components": [c.to_dict() for c in self.components], "unknown": self.unknown, "stale": self.stale, "notes": self.notes}


def _holder(a) -> str:
    h = getattr(a, "holders", None)
    if isinstance(h, list):
        h = [x for x in h if x]
        if len(h) > 1:
            return "joint"
        if h:
            return h[0]
    elif isinstance(h, str) and h:
        return "joint" if h.lower() in ("joint", "both", "all") else h
    one = getattr(a, "holder", None)
    return one or UNASSIGNED


def account_category(acc) -> str:
    return "savings" if (acc.purpose or "") == "savings" else "cash"


def _months_ago(today: dt.date, n: int) -> dt.date:
    return add_months(today, -n)


def build(ds, today: Optional[dt.date] = None, *, asset_stale_months: int = 3, liability_stale_months: int = 6,
          balance_stale_days: int = BALANCE_STALE_DAYS, schedules: Optional[dict] = None) -> NetWorth:
    """The net worth of a Dataset on `today`. `schedules`: liability id -> LoanSchedule (computed when absent)."""
    today = today or ds.today
    a_cut, l_cut = _months_ago(today, asset_stale_months), _months_ago(today, liability_stale_months)
    comps: list[Component] = []
    unknown: list[dict] = []
    stale: list[dict] = []
    for acc in ds.accounts_in(None):
        b = ds.balance_of(acc.uid)
        owner = acc.owner or UNASSIGNED
        if b is None:
            comps.append(Component("account", acc.uid, acc.label, account_category(acc), owner, "unknown",
                                   note="no balance synced yet"))
            unknown.append({"type": "account", "id": acc.uid, "label": acc.label, "reason": "no balance synced yet"})
            continue
        st = (today - b.as_of).days > balance_stale_days
        note = None if b.type in BOOKED else f"{b.type} balance (not a booked one)"
        comps.append(Component("account", acc.uid, acc.label, account_category(acc), owner, "known", b.amount_c, b.as_of, st,
                               "balance", b.type, note))
        if st:
            stale.append({"type": "account", "id": acc.uid, "label": acc.label})
    for a in ds.memory.assets:
        label = a.description or a.id
        cat = category_of_asset(a.kind)
        owner = _holder(a)
        if a.connected:
            continue                                  # counted through the balance of its bank account
        v = a.amount
        if v is None:
            comps.append(Component("asset", a.id, label, cat, owner, "unknown", note="no value recorded", kind=a.kind))
            unknown.append({"type": "asset", "id": a.id, "label": label, "reason": "no value recorded"})
            continue
        st = a.as_of is None or a.as_of < a_cut
        comps.append(Component("asset", a.id, label, cat, owner, "known", int(round(v * 100)), a.as_of, st, "declared",
                               note=("value date unknown" if a.as_of is None else None), kind=a.kind))
        if st:
            stale.append({"type": "asset", "id": a.id, "label": label})
    scheds = schedules or {}
    for _rel, lb in ds.memory.liabilities:
        label = lb.lender or lb.id
        owner = _holder(lb)
        sch = scheds.get(lb.id) or S.compute(lb, today)
        if sch.status == "not_applicable":             # a lease (LOA / LLD) owes no capital: the rents are a commitment, not a debt
            remaining = None
            if lb.end_date and lb.end_date > today and lb.monthly_payment:
                remaining = S.months_left_instalments(lb, today) * int(round(lb.monthly_payment * 100))
            comps.append(Component("liability", lb.id, label, "liability", owner, "excluded", remaining, None, False, None, None,
                                   "lease: no capital owed, not counted" + ("; the remaining rents are a commitment shown as the amount"
                                                                           if remaining is not None else ""), lb.kind))
            continue
        if lb.end_date and lb.end_date < today and lb.outstanding is None and sch.status != "computed":
            continue                                  # a closed loan with nothing recorded: nothing owed
        if sch.status == "computed" and sch.remaining_capital_c is not None:
            src = sch.source or "schedule"
            note = "computed from the amortization schedule" + (" (variable rate: approximate)" if sch.approximate else "")
            if src == "declared_rolled":
                note = f"the declared capital of {lb.outstanding_as_of} rolled forward with the loan's rate and instalments"
            elif src == "declared":
                note = f"the declared capital of {lb.outstanding_as_of} (it cannot be rolled forward)"
            if sch.outstanding_check:
                note += (f"; it differs from the theoretical table ({sch.outstanding_check.get('theoretical_capital_today')} EUR today): "
                         "early repayment or renegotiation? update the loan")
            comps.append(Component("liability", lb.id, label, "liability", owner, "known", sch.remaining_capital_c,
                                   lb.outstanding_as_of if src != "schedule" else today, False, src, None, note, lb.kind))
        elif lb.outstanding is not None:
            st = lb.outstanding_as_of is None or lb.outstanding_as_of < l_cut
            comps.append(Component("liability", lb.id, label, "liability", owner, "known", int(round(lb.outstanding * 100)),
                                   lb.outstanding_as_of, st, "declared", None, "declared capital, no schedule", lb.kind))
            if st:
                stale.append({"type": "liability", "id": lb.id, "label": label})
        else:
            why = "capital unknown: " + ", ".join(sch.missing[:4]) if sch.missing else "capital unknown"
            comps.append(Component("liability", lb.id, label, "liability", owner, "unknown", note=why, kind=lb.kind))
            unknown.append({"type": "liability", "id": lb.id, "label": label, "reason": why})
    by_cat = {c: 0 for c in CATEGORIES}
    by_cat["liabilities"] = 0
    owners: dict[str, dict] = {}
    for c in comps:
        o = owners.setdefault(c.owner, {"assets_c": 0, "liabilities_c": 0, "net_worth_c": 0, "n_unknown": 0})
        if c.status == "excluded":
            continue
        if c.status != "known":
            o["n_unknown"] += 1
            continue
        if c.type == "liability":
            by_cat["liabilities"] += c.amount_c
            o["liabilities_c"] += c.amount_c
        else:
            by_cat[c.category] += c.amount_c
            o["assets_c"] += c.amount_c
    for o in owners.values():
        o["net_worth_c"] = o["assets_c"] - o["liabilities_c"]
    assets = sum(by_cat[c] for c in CATEGORIES)
    notes = ["only what is known is added up: an unknown item is listed and NOT counted, so the real figure differs"] if unknown else []
    return NetWorth(today, assets - by_cat["liabilities"], assets, by_cat["liabilities"], not unknown, by_cat,
                    dict(sorted(owners.items())), comps, unknown, stale, len(unknown), notes)
