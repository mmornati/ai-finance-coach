"""A rental property and what belongs to it (E15-1).

A property is an asset of kind ``real_estate_rental`` in ``assets.yaml``. Its bank account is the one the asset names (``account``: uid or
label), else, when there is exactly one rental property still without one and exactly one account flagged ``purpose: rental`` that no property
names, that account (the link is then reported as ``only_one`` so the user can confirm it). Its loan is the liability named by ``loan``, else the
loans whose ``asset`` is the property id, else the one mortgage debited from the property account. A transaction belongs to the property when it
is on its account, or whatever its account when it carries the tag ``property-<id>`` (costs paid from the main account: a tag annotation).

A flow of the property account is put in one BUCKET by its category:

    rent        income.rental                                   loan       housing.rental_property_loan (+ the linked loan's own payments)
    charges     housing.property_charges (co-ownership)         fees       housing.property_management
    taxes       housing.property_tax, taxes.taxes               insurance  housing.property_insurance, housing.home_insurance, insurance.other
    works       housing.renovation, housing.maintenance_diy     transfer   transfer.* (the owner funding the account: never rent, never a cost)
    other       anything else on the account (listed apart so that nothing is silently dropped)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

RENTAL_KIND = "real_estate_rental"
RENTAL_PURPOSE = "rental"
TAG_PREFIX = "property-"
COST_BUCKETS = ("loan", "charges", "fees", "taxes", "insurance", "works", "other")
BUCKETS = ("rent",) + COST_BUCKETS
CATEGORY_BUCKET = {
    "income.rental": "rent",
    "housing.rental_property_loan": "loan",
    "housing.property_charges": "charges",
    "housing.property_management": "fees",
    "housing.property_tax": "taxes", "taxes.taxes": "taxes",
    "housing.property_insurance": "insurance", "housing.home_insurance": "insurance", "insurance.other": "insurance",
    "housing.renovation": "works", "housing.maintenance_diy": "works",
}
PROPERTY_CATEGORIES = tuple(CATEGORY_BUCKET)
REVIEW_EXEMPT = frozenset({"fees.bank_fees"})            # in the "other flows" line, but nothing to label
LABEL = {"rent": "rent received", "loan": "loan instalments", "charges": "co-ownership charges", "fees": "management fees",
         "taxes": "property tax", "insurance": "insurance (PNO, GLI)", "works": "works and repairs", "other": "other flows"}


def tag_of(prop_id: str) -> str:
    return TAG_PREFIX + prop_id


def bucket_of(category: str) -> str:
    """The bucket of a category on a property account; ``transfer`` for the owner's own movements."""
    b = CATEGORY_BUCKET.get(category)
    if b:
        return b
    return "transfer" if category.startswith("transfer.") else "other"


@dataclass
class Property:
    id: str
    asset: object                                   # memory.schemas.Asset
    accounts: list = field(default_factory=list)    # uids
    account_link: str = "none"                      # declared | only_one | none | unknown (the declared account is not in the data)
    loans: list = field(default_factory=list)       # [Liability]
    loan_link: str = "none"                         # declared | asset | account | none | unknown
    notes: list = field(default_factory=list)

    @property
    def commitment(self):
        return getattr(self.asset, "commitment", None)


def rental_assets(ds) -> list:
    return [a for a in ds.memory.assets if a.kind == RENTAL_KIND]


def _loan_candidates(ds, asset, accounts: list) -> tuple[list, str, list]:
    lbs = [lb for _rel, lb in ds.memory.liabilities]
    notes: list = []
    if getattr(asset, "loan", None):
        hit = [lb for lb in lbs if lb.id == asset.loan]
        if hit:
            return hit, "declared", notes
        notes.append("the loan named on the property is not in the loan files")
        return [], "unknown", notes
    hit = [lb for lb in lbs if (lb.asset or "").strip().lower() == asset.id.lower()]
    if hit:
        return hit, "asset", notes
    if accounts:
        from coach.analytics.forecast import resolve_liability_account
        by_acc = [lb for lb in lbs if lb.kind == "mortgage" and resolve_liability_account(ds, lb) in accounts]
        if len(by_acc) == 1:
            return by_acc, "account", notes
    return [], "none", notes


def properties(ds) -> list[Property]:
    """Every rental property of the memory with its links resolved (cached per Dataset)."""
    got = ds._cache.get("rental_properties")
    if got is not None:
        return got
    assets = rental_assets(ds)
    out: list[Property] = []
    claimed: set = set()
    for a in assets:
        p = Property(a.id, a)
        ref = (getattr(a, "account", None) or "").strip()
        if ref:
            acc = ds.resolve_account(ref)
            if acc is None:
                p.account_link = "unknown"
                p.notes.append("the account named on the property is not in the data (excluded, to review, or a wrong uid / label)")
            else:
                p.accounts, p.account_link = [acc.uid], "declared"
                claimed.add(acc.uid)
        out.append(p)
    free_acc = [u for u, x in sorted(ds.accounts.items()) if (x.purpose or "") == RENTAL_PURPOSE and u not in claimed]
    free_props = [p for p in out if p.account_link == "none"]
    if len(free_props) == 1 and len(free_acc) == 1:
        free_props[0].accounts, free_props[0].account_link = [free_acc[0]], "only_one"
    for p in out:
        p.loans, p.loan_link, n = _loan_candidates(ds, p.asset, p.accounts)
        p.notes += n
    ds._cache["rental_properties"] = out
    return out


def unlinked_rental_accounts(ds) -> list:
    """Accounts flagged ``purpose: rental`` that no property uses."""
    used = {u for p in properties(ds) for u in p.accounts}
    return [u for u, x in sorted(ds.accounts.items()) if (x.purpose or "") == RENTAL_PURPOSE and u not in used]


def find(ds, ident: Optional[str]) -> Property:
    ps = properties(ds)
    if ident is None:
        if len(ps) == 1:
            return ps[0]
        raise LookupError("several rental properties: name one (" + ", ".join(p.id for p in ps) + ")" if ps else "no rental property on file")
    hit = [p for p in ps if p.id == ident]
    if not hit:
        raise LookupError(f"no rental property with id {ident!r}" + (f" (known: {', '.join(p.id for p in ps)})" if ps else ""))
    return hit[0]


def attributed(ds) -> dict:
    """property id -> [Tx] (cached): the transactions of its account(s) and those tagged ``property-<id>``; plus ``None`` -> the
    transactions of an account shared by several properties that carry no property tag (reported, never guessed)."""
    got = ds._cache.get("rental_attributed")
    if got is not None:
        return got
    ps = properties(ds)
    by_tag = {tag_of(p.id): p.id for p in ps}
    by_acc: dict = {}
    for p in ps:
        for u in p.accounts:
            by_acc.setdefault(u, []).append(p.id)
    out: dict = {p.id: [] for p in ps}
    out[None] = []
    for t in ds.txs:
        tagged = [by_tag[x] for x in t.tags if x in by_tag]
        if tagged:
            out[tagged[0]].append(t)
        elif t.account in by_acc:
            owners = by_acc[t.account]
            out[owners[0] if len(owners) == 1 else None].append(t)
    ds._cache["rental_attributed"] = out
    return out
