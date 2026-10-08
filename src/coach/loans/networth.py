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
from coach.i18n_msg import server_msg
from coach.loans import schedule as S

CATEGORIES = ("cash", "savings", "investments", "real_estate", "vehicles", "other")
BOOKED = ("CLBD", "ITBD", "OPBD")
ASSET_CATEGORY = {
    "regulated_savings": "savings", "savings_account": "savings", "employee_savings_plan": "investments",
    "life_insurance_savings": "investments", "securities": "investments", "pension": "investments", "crypto": "investments",
    "real_estate": "real_estate", "real_estate_rental": "real_estate", "vehicle": "vehicles", "cash": "cash",
}
# what the coach reads (MCP, English); the web names a kind with the item form's options (itemForm.option.*Kind of common.json)
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
    note_msg: Optional[dict] = None  # the note for the web app (coach.i18n_msg): {code, params, text}


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
    unknown: list                   # [{type, id, label, reason, reason_msg}]
    stale: list                     # [{type, id, label}]
    n_unknown: int = 0
    notes: list = field(default_factory=list)
    notes_msg: list = field(default_factory=list)   # the notes for the web app, same order

    def to_dict(self) -> dict:
        return {"as_of": self.as_of.isoformat(), "net_worth": money_str(self.net_worth_c), "assets": money_str(self.assets_c),
                "liabilities": money_str(self.liabilities_c), "complete": self.complete, "n_unknown": self.n_unknown,
                "by_category": {k: money_str(v) for k, v in self.by_category_c.items()},
                "by_owner": {o: {"assets": money_str(v["assets_c"]), "liabilities": money_str(v["liabilities_c"]),
                                 "net_worth": money_str(v["net_worth_c"]), "n_unknown": v["n_unknown"]} for o, v in self.by_owner.items()},
                "components": [c.to_dict() for c in self.components], "unknown": self.unknown, "stale": self.stale, "notes": self.notes,
                "notes_msg": self.notes_msg}


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


def _unknown(type_: str, id_: str, label: str, msg: dict) -> dict:
    return {"type": type_, "id": id_, "label": label, "reason": msg["text"], "reason_msg": msg}


def _liability_note(src: str, approximate: bool, declared_date, check: Optional[dict]) -> dict:
    """The note of a liability counted from its schedule (or its declared capital): a message whose text is the English note."""
    if src == "declared_rolled":
        text, code = f"the declared capital of {declared_date} rolled forward with the loan's rate and instalments", "netWorth.declaredRolled"
    elif src == "declared":
        text, code = f"the declared capital of {declared_date} (it cannot be rolled forward)", "netWorth.declaredAsIs"
    elif approximate:
        text, code = "computed from the amortization schedule (variable rate: approximate)", "netWorth.fromScheduleApprox"
    else:
        text, code = "computed from the amortization schedule", "netWorth.fromSchedule"
    theoretical = None
    if check:
        theoretical = check.get("theoretical_capital_today")
        text += (f"; it differs from the theoretical table ({theoretical} EUR today): "
                 "early repayment or renegotiation? update the loan")
        code += "Differs"                     # netWorth.fromScheduleDiffers, netWorth.declaredRolledDiffers, ... (server.json)
    return server_msg(code, text, declared_date=declared_date, theoretical_amount=theoretical)


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
            m = server_msg("netWorth.noBalance", "no balance synced yet")
            comps.append(Component("account", acc.uid, acc.label, account_category(acc), owner, "unknown", note=m["text"], note_msg=m))
            unknown.append(_unknown("account", acc.uid, acc.label, m))
            continue
        st = (today - b.as_of).days > balance_stale_days
        m = None if b.type in BOOKED else server_msg("netWorth.nonBooked", f"{b.type} balance (not a booked one)", balance_type=b.type)
        comps.append(Component("account", acc.uid, acc.label, account_category(acc), owner, "known", b.amount_c, b.as_of, st,
                               "balance", b.type, m and m["text"], note_msg=m))
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
            m = server_msg("netWorth.noValue", "no value recorded")
            comps.append(Component("asset", a.id, label, cat, owner, "unknown", note=m["text"], kind=a.kind, note_msg=m))
            unknown.append(_unknown("asset", a.id, label, m))
            continue
        st = a.as_of is None or a.as_of < a_cut
        m = server_msg("netWorth.valueDateUnknown", "value date unknown") if a.as_of is None else None
        comps.append(Component("asset", a.id, label, cat, owner, "known", int(round(v * 100)), a.as_of, st, "declared",
                               note=m and m["text"], kind=a.kind, note_msg=m))
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
            m = (server_msg("netWorth.leaseRents", "lease: no capital owed, not counted; the remaining rents are a commitment shown as the amount")
                 if remaining is not None else server_msg("netWorth.lease", "lease: no capital owed, not counted"))
            comps.append(Component("liability", lb.id, label, "liability", owner, "excluded", remaining, None, False, None, None,
                                   m["text"], lb.kind, m))
            continue
        if lb.end_date and lb.end_date < today and lb.outstanding is None and sch.status != "computed":
            continue                                  # a closed loan with nothing recorded: nothing owed
        if sch.status == "computed" and sch.remaining_capital_c is not None:
            src = sch.source or "schedule"
            m = _liability_note(src, sch.approximate, lb.outstanding_as_of, sch.outstanding_check)
            comps.append(Component("liability", lb.id, label, "liability", owner, "known", sch.remaining_capital_c,
                                   lb.outstanding_as_of if src != "schedule" else today, False, src, None, m["text"], lb.kind, m))
        elif lb.outstanding is not None:
            st = lb.outstanding_as_of is None or lb.outstanding_as_of < l_cut
            m = server_msg("netWorth.declaredNoSchedule", "declared capital, no schedule")
            comps.append(Component("liability", lb.id, label, "liability", owner, "known", int(round(lb.outstanding * 100)),
                                   lb.outstanding_as_of, st, "declared", None, m["text"], lb.kind, m))
            if st:
                stale.append({"type": "liability", "id": lb.id, "label": label})
        else:
            fields = ", ".join(sch.missing[:4])
            m = (server_msg("netWorth.capitalUnknownFields", "capital unknown: " + fields, fields=fields)
                 if sch.missing else server_msg("netWorth.capitalUnknown", "capital unknown"))
            comps.append(Component("liability", lb.id, label, "liability", owner, "unknown", note=m["text"], kind=lb.kind, note_msg=m))
            unknown.append(_unknown("liability", lb.id, label, m))
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
    msgs = [server_msg("netWorth.knownOnly", "only what is known is added up: an unknown item is listed and NOT counted, so the real "
                                             "figure differs")] if unknown else []
    return NetWorth(today, assets - by_cat["liabilities"], assets, by_cat["liabilities"], not unknown, by_cat,
                    dict(sorted(owners.items())), comps, unknown, stale, len(unknown), [m["text"] for m in msgs], msgs)
