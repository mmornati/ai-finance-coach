"""Total Cost of Ownership (TCO) for physical assets (vehicles, real estate, etc.).

Aggregates:
- Financing: loans, LOA/LLD lease contracts linked to the asset via `liability.asset`.
- Contracts: recurring insurance, maintenance and services linked via `contract.asset`.
- Operating expenses: transactions tagged to the asset via annotations (`tx.asset`).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Optional

from coach.analytics.common import Result, to_cents
from coach.analytics.recurring import tx_matches
from coach.loans import service as loans_service


@dataclass
class LiabilityCost(Result):
    id: str
    kind: str
    lender: Optional[str]
    monthly_payment_c: Optional[int]
    first_payment_c: Optional[int]
    residual_value_c: Optional[int]
    term_months: Optional[int]
    start_date: Optional[dt.date]
    end_date: Optional[dt.date]
    total_financing_c: Optional[int]
    documents: list[str] = field(default_factory=list)


@dataclass
class ContractCost(Result):
    id: str
    kind: Optional[str]
    provider: Optional[str]
    period: str
    billing_amount_c: int
    monthly_c: Optional[int]
    annual_c: Optional[int]
    documents: list[str] = field(default_factory=list)


@dataclass
class OperatingExpense(Result):
    tx_key: str
    date: dt.date
    amount_c: int
    category: str
    desc: str
    merchant: Optional[str]


@dataclass
class AssetTco(Result):
    asset_id: str
    asset_kind: str
    description: Optional[str]
    purchase_price_c: Optional[int]
    purchase_date: Optional[dt.date]
    liabilities: list[LiabilityCost]
    contracts: list[ContractCost]
    expenses_count: int
    expenses_total_c: int
    expenses_monthly_avg_c: int
    expenses_by_category: dict[str, int]
    recent_expenses: list[OperatingExpense]
    financing_monthly_c: int
    contracts_monthly_c: int
    fixed_monthly_c: int
    operating_monthly_avg_c: int
    total_monthly_c: int
    financing_paid_to_date_c: Optional[int] = None
    contracts_paid_to_date_c: Optional[int] = None
    total_spent_to_date_c: Optional[int] = None
    projected_total_c: Optional[int] = None
    projected_is_estimate: bool = False
    notes: list[str] = field(default_factory=list)


def _contract_monthly_cents(c) -> tuple[str, int, Optional[int]]:
    """Returns (period, billing_amount_c, monthly_equivalent_c).

    Unrecognised billing periods return None for monthly equivalent so that
    unknowns are made explicit rather than silently assumed monthly.
    """
    if not c.billing or c.billing.amount is None:
        return "none", 0, 0
    amt_c = to_cents(c.billing.amount)
    period = (c.billing.period or "monthly").lower().strip()
    if period in ("monthly", "month"):
        return "monthly", amt_c, amt_c
    if period in ("yearly", "annual", "year"):
        return period, amt_c, round(amt_c / 12)
    if period in ("quarterly", "quarter"):
        return period, amt_c, round(amt_c / 3)
    if period in ("bimonthly",):
        return period, amt_c, round(amt_c / 2)
    if period in ("biannual", "semi-annual"):
        return period, amt_c, round(amt_c / 6)
    if period in ("weekly", "week"):
        return period, amt_c, round(amt_c * 52 / 12)
    return period, amt_c, None


def compute_asset_tco(ds, asset_id: str, as_of: Optional[dt.date] = None) -> dict:
    """Compute the total cost of ownership for an asset.

    Respects the project rule: unknowns are listed, never estimated.
    Paid-to-date comes from matched bank transactions in ds.whole (or computed schedule),
    never from months elapsed.
    """
    asset = next((a for a in ds.memory.assets if a.id == asset_id), None)
    if asset is None:
        return {"error": f"Asset '{asset_id}' not found in memory"}

    today = as_of or ds.today
    notes: list[str] = []
    matched_tx_keys: set[str] = set()

    # 1. Linked liabilities (only match l.asset == asset_id)
    liabilities_cost: list[LiabilityCost] = []
    financing_monthly_c = 0
    financing_total_c = 0
    earliest_start: Optional[dt.date] = asset.purchase_date

    financing_paid_to_date_c: Optional[int] = 0
    financing_has_unknown = False
    has_liabilities = False

    for _, l in ds.memory.liabilities:
        if getattr(l, "asset", None) == asset_id:
            has_liabilities = True
            m_amt_c = to_cents(l.monthly_payment) if l.monthly_payment is not None else None
            fp_c = to_cents(l.first_payment) if l.first_payment is not None else None
            rv_c = to_cents(l.residual_value) if l.residual_value is not None else 0
            if m_amt_c is not None:
                financing_monthly_c += m_amt_c

            tot_fin_c = None
            if l.term_months and m_amt_c is not None:
                first = fp_c if fp_c is not None else m_amt_c
                remaining_months = max(0, l.term_months - 1)
                tot_fin_c = first + (remaining_months * m_amt_c) + rv_c
                financing_total_c += tot_fin_c
            elif l.principal:
                tot_fin_c = to_cents(l.principal)
                financing_total_c += tot_fin_c

            if l.start_date and (earliest_start is None or l.start_date < earliest_start):
                earliest_start = l.start_date

            liabilities_cost.append(LiabilityCost(
                id=l.id,
                kind=l.kind,
                lender=l.lender,
                monthly_payment_c=m_amt_c,
                first_payment_c=fp_c,
                residual_value_c=rv_c if rv_c > 0 else None,
                term_months=l.term_months,
                start_date=l.start_date,
                end_date=l.end_date,
                total_financing_c=tot_fin_c,
                documents=list(l.documents) if l.documents else [],
            ))

            # Matched payments in bank data
            obs = [p for p in loans_service.observed_of(ds, l) if p.date <= today]
            if obs:
                for p in obs:
                    matched_tx_keys.add(p.tx_key)
                if financing_paid_to_date_c is not None:
                    financing_paid_to_date_c += sum(p.amount_c for p in obs)
            else:
                sch = loans_service.schedule_of(ds, l)
                if sch.status == "computed" and sch.rows:
                    sched_made = sum(r.total_c for r in sch.rows if r.due <= today)
                    if financing_paid_to_date_c is not None:
                        financing_paid_to_date_c += sched_made
                else:
                    financing_has_unknown = True
                    notes.append(f"Financing paid-to-date for liability '{l.id}' is unknown (no matched transactions and no schedule).")

    if financing_has_unknown:
        financing_paid_to_date_c = None
    elif not has_liabilities:
        financing_paid_to_date_c = 0

    # 2. Linked contracts (only match c.asset == asset_id)
    contracts_cost: list[ContractCost] = []
    contracts_monthly_c = 0
    contracts_paid_to_date_c = 0
    tx_pool = getattr(ds, "whole", None) or ds.txs

    for _, c in ds.memory.contracts:
        if getattr(c, "asset", None) == asset_id:
            period, b_amt_c, m_c = _contract_monthly_cents(c)
            if m_c is not None:
                contracts_monthly_c += m_c
                ann_c = m_c * 12
            else:
                ann_c = None
                notes.append(f"Contract '{c.id}' has unrecognised billing period '{period}'; monthly equivalent is unknown.")

            contracts_cost.append(ContractCost(
                id=c.id,
                kind=c.kind,
                provider=c.provider,
                period=period,
                billing_amount_c=b_amt_c,
                monthly_c=m_c,
                annual_c=ann_c,
                documents=list(c.documents) if c.documents else [],
            ))

            # Match actual contract debits in bank transactions
            c_txs = []
            if c.merchant_match:
                try:
                    pat = re.compile(c.merchant_match, re.I)
                    for t in tx_pool:
                        if t.amount_c < 0 and t.date <= today and tx_matches(pat, c.amount_match, t):
                            c_txs.append(t)
                            matched_tx_keys.add(t.key)
                except re.error:
                    notes.append(f"Contract '{c.id}' has invalid merchant_match regex: {c.merchant_match!r}.")

            if c_txs:
                contracts_paid_to_date_c += sum(abs(t.amount_c) for t in c_txs)
            elif c.merchant_match:
                notes.append(f"Contract '{c.id}' has merchant_match '{c.merchant_match}' but no matching transactions found.")
            else:
                notes.append(f"Contract '{c.id}' has no merchant_match; paid-to-date from bank transactions cannot be determined.")

    # 3. Operating expenses from transactions tagged with asset
    matching_txs = [t for t in ds.txs if getattr(t, "asset", None) == asset_id and t.amount_c < 0 and t.date <= today]

    # Exclude direct liability payments or contract payments already counted
    liability_merchants = {l.lender.upper() for l in liabilities_cost if l.lender}
    op_txs = [
        t for t in matching_txs
        if t.key not in matched_tx_keys and not (t.merchant and t.merchant.upper() in liability_merchants)
    ]

    expenses_total_c = sum(abs(t.amount_c) for t in op_txs)
    expenses_count = len(op_txs)

    expenses_by_cat: dict[str, int] = {}
    for t in op_txs:
        cat = t.category or "other"
        expenses_by_cat[cat] = expenses_by_cat.get(cat, 0) + abs(t.amount_c)

    min_date = earliest_start or (min((t.date for t in op_txs), default=today) if op_txs else today)
    days_span = max((today - min_date).days, 30)
    months_span = max(round(days_span / 30.4375), 1)

    expenses_monthly_avg_c = round(expenses_total_c / months_span) if expenses_total_c > 0 else 0

    recent_expenses = [
        OperatingExpense(
            tx_key=t.key,
            date=t.date,
            amount_c=t.amount_c,
            category=t.category,
            desc=t.desc,
            merchant=t.merchant,
        )
        for t in sorted(op_txs, key=lambda x: x.date, reverse=True)[:10]
    ]

    # Summary
    purchase_price_c = to_cents(asset.purchase_price) if asset.purchase_price is not None else None
    fixed_monthly_c = financing_monthly_c + contracts_monthly_c
    total_monthly_c = fixed_monthly_c + expenses_monthly_avg_c

    # Total spent to date
    if financing_paid_to_date_c is not None:
        total_spent_to_date_c = (
            (purchase_price_c if not liabilities_cost and purchase_price_c else 0)
            + financing_paid_to_date_c
            + contracts_paid_to_date_c
            + expenses_total_c
        )
    else:
        total_spent_to_date_c = None
        notes.append("Total spent to date is unknown because financing paid-to-date is unknown.")

    # Projected total: never guess horizon (e.g. 36 or 60 months)
    projected_term_months = None
    for l_cost in liabilities_cost:
        if l_cost.term_months:
            projected_term_months = max(projected_term_months or 0, l_cost.term_months)

    projected_total_c = None
    projected_is_estimate = False
    if projected_term_months is not None:
        if financing_total_c or purchase_price_c:
            base_fin = financing_total_c if financing_total_c else (purchase_price_c or 0)
            proj_contracts = contracts_monthly_c * projected_term_months
            proj_operating = expenses_monthly_avg_c * projected_term_months
            projected_total_c = base_fin + proj_contracts + proj_operating
            projected_is_estimate = True
            notes.append(f"Projected total is an estimate based on {projected_term_months}-month financing term.")
    else:
        notes.append("Projected total is unknown (term is unknown; no assumed horizon is used).")

    res = AssetTco(
        asset_id=asset.id,
        asset_kind=asset.kind,
        description=asset.description or asset.id,
        purchase_price_c=purchase_price_c,
        purchase_date=asset.purchase_date,
        liabilities=liabilities_cost,
        contracts=contracts_cost,
        expenses_count=expenses_count,
        expenses_total_c=expenses_total_c,
        expenses_monthly_avg_c=expenses_monthly_avg_c,
        expenses_by_category=expenses_by_cat,
        recent_expenses=recent_expenses,
        financing_monthly_c=financing_monthly_c,
        contracts_monthly_c=contracts_monthly_c,
        fixed_monthly_c=fixed_monthly_c,
        operating_monthly_avg_c=expenses_monthly_avg_c,
        total_monthly_c=total_monthly_c,
        financing_paid_to_date_c=financing_paid_to_date_c,
        contracts_paid_to_date_c=contracts_paid_to_date_c,
        total_spent_to_date_c=total_spent_to_date_c,
        projected_total_c=projected_total_c,
        projected_is_estimate=projected_is_estimate,
        notes=notes,
    )
    return res.to_dict()
