"""Pydantic schemas of the memory files (E3). One model per file kind.

Policy: the files are written by hand, by the UI and by the coach, so
 * a value of the wrong TYPE, an unknown enum value, a bad regex or a duplicate id is an ERROR;
 * annotations and their ``match`` block REJECT unknown keys (a typo such as ``merchant:`` would silently never
   match anything);
 * assets / liabilities / contracts / household keep unknown extra keys (users add their own notes) and the check
   reports them as ``info``.
Semantic checks that need the rest of the world (category exists, event exists, regexes match transactions) live in
:mod:`coach.memory.check`.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
KNOWN_TAGS = {"one_off", "exclude_from_averages", "capital", "reimbursable", "savings", "investment"}   # documented in memory/README.md; analytics also reads savings / investment (transfers counted as saved)

Scalar = Union[str, int, float, bool, None, dt.date]


def _slug(v: Any, what: str = "id") -> str:
    if not isinstance(v, str) or not SLUG_RE.match(v):
        raise ValueError(f"{what} must be lowercase letters, digits, '-' or '_' (got {v!r})")
    return v


ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
Number = Union[StrictInt, StrictFloat]


def _iso_date(v):
    """Dates in annotations are ISO calendar dates: a date, or a 'YYYY-MM-DD' string. A date WITH a time is refused
    (the classifier compares booking dates; a time would silently shift a day)."""
    if v is None or (isinstance(v, dt.date) and not isinstance(v, dt.datetime)):
        return v
    if isinstance(v, dt.datetime):
        raise ValueError("a date with a time is not allowed here: use YYYY-MM-DD")
    if isinstance(v, str) and ISO_DATE_RE.match(v):
        try:
            return dt.date.fromisoformat(v)
        except ValueError:
            pass
    raise ValueError(f"must be an ISO date YYYY-MM-DD (got {v!r})")


_SAMPLE_KEYS = [
    "CARREFOUR MARKET NANTES", "AMAZON PRIME PARIS", "EDF CLIENTS", "NETFLIX COM", "SNCF INTERNET PARIS",
    "LECLERC DRIVE ORVAULT", "SPORTMAX CARQUEFOU", "BOULANGERIE PAUL NANTES", "TOTAL ENERGIES STATION", "ORANGE SA",
    "FREE MOBILE", "SPOTIFY SE", "PHARMACIE DU CENTRE", "AIRBNB PARIS", "IKEA REZE", "LEROY MERLIN ORVAULT", "UBER TRIP",
    "BOOKING COM AMSTERDAM", "MCDONALDS BOUGUENAIS", "AUCHAN SAINT HERBLAIN", "ECH PRET 123456", "PRLV CREDITEST",
    "VIR SEPA SALAIRE ACME", "RET DAB BANQUE", "PAYPAL EBAY", "CAFE DE LA GARE", "RELAY BEAUVAIS", "DOCTEUR MARTIN CABINET",
    "CIC MONTHLY FUNDING", "KERVALIS FRANCE", "SURANDIE", "ORMEKO", "UPLINE", "LA MAISON VERTOU", "SQ *COFFEE SHOP", "APPLE COM BILL",
    ]
_SAMPLE_JUNK = ["ZQ7#KX", "ABC DEF 123", "É-_/ 9", "FR 12", "X", "12345", "", " "]


def _regex(v: Optional[str], what: str) -> Optional[str]:
    if v is not None:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{what}: an empty pattern matches every transaction; give a real regular expression")
        try:
            rx = re.compile(v, re.I)
        except re.error as e:
            raise ValueError(f"{what}: invalid regular expression {v!r} ({e})") from e
        hit = sum(1 for k in _SAMPLE_KEYS if rx.search(k))
        if hit / len(_SAMPLE_KEYS) >= 0.9 or all(rx.search(k) for k in _SAMPLE_JUNK):
            raise ValueError(f"{what}: the pattern {v!r} matches {hit} of {len(_SAMPLE_KEYS)} typical merchant keys (or every test string): it "
                             "would apply to (almost) every transaction; name what it must contain")
    return v


class Base(BaseModel):
    # NaN / inf are refused everywhere: a `.nan` or `.inf` in a hand-edited file would pass a plain float check and
    # crash (or silently poison) every sum computed from it
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True, allow_inf_nan=False)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def _unique_ids(items: list, what: str) -> None:
    seen: set = set()
    for it in items:
        i = getattr(it, "id", None)
        if i is None:
            continue
        if i in seen:
            raise ValueError(f"duplicate {what} id {i!r}")
        seen.add(i)


# ---------------------------------------------------------------- categorization.yaml

class Match(Strict):
    """Strict types on purpose: the classifier consumes THESE validated values (one loader), so a quoted number or
    a stray type can never reach the matcher."""
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    merchant_key: Optional[StrictStr] = None
    description: Optional[StrictStr] = None
    tx_keys: Optional[list[StrictStr]] = None
    date_from: Optional[dt.date] = None
    date_to: Optional[dt.date] = None
    amount_min: Optional[Number] = None
    amount_max: Optional[Number] = None
    category_in: Optional[list[StrictStr]] = None
    weekdays: Optional[list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]]] = None

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def _dates(cls, v):
        return _iso_date(v)

    @field_validator("merchant_key")
    @classmethod
    def _mk(cls, v):
        return _regex(v, "merchant_key")

    @field_validator("description")
    @classmethod
    def _desc(cls, v):
        return _regex(v, "description")

    @field_validator("tx_keys")
    @classmethod
    def _txk(cls, v):
        if v is not None and (not v or any(not k.strip() for k in v)):
            raise ValueError("tx_keys must be a non-empty list of non-empty keys")
        return v

    @model_validator(mode="after")
    def _non_empty(self):
        given = {k: v for k, v in self.model_dump().items() if v is not None}
        if not given:
            raise ValueError("match must contain at least one criterion (merchant_key, description, tx_keys, "
                             "date_from/date_to, amount_min/amount_max, category_in, weekdays)")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from is after date_to")
        if self.amount_min is not None and self.amount_max is not None and self.amount_min > self.amount_max:
            raise ValueError("amount_min is greater than amount_max (amounts are signed: money out is negative)")
        return self


class Annotation(Strict):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    id: StrictStr
    match: Match
    category: Optional[StrictStr] = None
    tags: list[StrictStr] = Field(default_factory=list)
    event: Optional[StrictStr] = None
    note: Optional[StrictStr] = None

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return _slug(v, "annotation id")

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v is not None and not re.match(r"^[a-z0-9_]+\.[a-z0-9_]+$", v):
            raise ValueError(f"category must look like group.leaf (got {v!r})")
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v):
        for t in v:
            if not re.match(r"^[a-z0-9][a-z0-9_-]*$", t):
                raise ValueError(f"tag {t!r} must be a lowercase word (letters, digits, '_' or '-')")
        return v

    @field_validator("event")
    @classmethod
    def _event(cls, v):
        return None if v is None else _slug(v, "event")

    @model_validator(mode="after")
    def _has_effect(self):
        if not (self.category or self.tags or self.event):
            raise ValueError("an annotation needs an effect: category, tags and/or event")
        return self


class CategorizationFile(Base):
    annotations: list[Annotation] = Field(default_factory=list)

    @field_validator("annotations", mode="before")
    @classmethod
    def _none(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _ids(self):
        _unique_ids(self.annotations, "annotation")
        return self


# ---------------------------------------------------------------- assets.yaml

ASSET_KINDS = {"regulated_savings", "savings_account", "employee_savings_plan", "life_insurance_savings", "vehicle",
               "real_estate", "real_estate_rental", "securities", "pension", "cash", "crypto", "other"}


class Extension(Base):
    """E15-3: what the owner decided at the end of the commitment (a recorded decision, never a guess)."""
    decision: Literal["undecided", "extend", "not_extend"] = "undecided"
    years: Optional[int] = Field(default=None, ge=1, le=30)      # length of the extension the owner chose (extend)
    additional_rate_pct: Optional[float] = Field(default=None, ge=0, le=100)   # extra reduction rate of the extension, as the user states it
    decided_on: Optional[dt.date] = None
    note: Optional[str] = None

    @field_validator("decided_on", mode="before")
    @classmethod
    def _d(cls, v):
        return _iso_date(v)


class ReductionSegment(Base):
    """E15-4: `years` years at `yearly_rate_pct` % of the eligible price a year (a scheme that spreads its reduction unevenly)."""
    years: int = Field(ge=1, le=30)
    yearly_rate_pct: float = Field(ge=0, le=100)


class Commitment(Base):
    """E15-3: the rental commitment of a tax-incentive scheme (Pinel-like). EVERY figure is what the owner states from the deed or the
    scheme's own table: the coach never looks anything up and never guesses a missing one (it asks)."""
    start_date: Optional[dt.date] = None            # first day of the commitment (the first lease)
    years: Optional[int] = Field(default=None, ge=1, le=30)      # 6 / 9 / 12 for Pinel-type schemes; any whole number is accepted
    end_date: Optional[dt.date] = None              # overrides start_date + years
    surface_m2: Optional[float] = Field(default=None, gt=0, le=100000)
    rent_cap_m2: Optional[float] = Field(default=None, ge=0, le=10000)       # monthly rent cap per m2 as the owner computed it (zone, coefficient)
    rent_cap_monthly: Optional[float] = Field(default=None, ge=0, le=1000000)  # or the monthly cap itself (wins over rent_cap_m2 x surface)
    tenant_income_limit: Optional[float] = Field(default=None, ge=0, le=100000000)   # yearly, for the tenant household's size, as stated
    tenant_income: Optional[float] = Field(default=None, ge=0, le=100000000)         # the current tenant's reference income, as declared
    reduction_rate_pct: Optional[float] = Field(default=None, ge=0, le=100)  # total reduction over the commitment (% of the eligible price)
    reduction_base_cap: Optional[float] = Field(default=None, ge=0)          # ceiling of the eligible price, as stated (e.g. a scheme ceiling)
    reduction_first_year: Optional[int] = Field(default=None, ge=1990, le=2100)   # first tax year the reduction applies to
    reduction_schedule: list[ReductionSegment] = Field(default_factory=list)      # optional uneven yearly schedule (wins over the total rate)
    extension: Optional[Extension] = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _dates(cls, v):
        return _iso_date(v)

    @field_validator("reduction_schedule", mode="before")
    @classmethod
    def _sched(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _order(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("commitment end_date is before start_date")
        return self


class MarketRate(Base):
    """E15-5: a market rate the owner typed in (no web lookup): % a year, the day it was seen and where."""
    rate_pct: float = Field(ge=0, le=25)
    as_of: Optional[dt.date] = None
    source: Optional[str] = None

    @field_validator("as_of", mode="before")
    @classmethod
    def _d(cls, v):
        return _iso_date(v)


class Vacancy(Base):
    """E15-2: a period the owner knows the property was not let (works, between two tenants): not reported as a missing rent."""
    start: dt.date
    end: Optional[dt.date] = None
    note: Optional[str] = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def _d(cls, v):
        return _iso_date(v)


class Asset(Base):
    id: str
    kind: str
    provider: Optional[str] = None
    holder: Optional[str] = None
    holders: Union[str, list[str], None] = None
    count: Optional[int] = None
    balance: Optional[float] = None
    value: Optional[float] = None
    as_of: Optional[dt.date] = None
    liquidity: Optional[str] = None
    connected: Optional[bool] = None
    bank_match: Optional[str] = None
    contribution_monthly: Optional[float] = None
    opened: Optional[dt.date] = None
    employer: Optional[str] = None
    description: Optional[str] = None
    purchase_price: Optional[float] = None
    purchase_date: Optional[dt.date] = None
    scheme: Optional[str] = None
    bank: Optional[str] = None
    pinel_commitment_years: Optional[int] = None
    rent_monthly: Optional[float] = None
    property_manager: Optional[str] = None
    # E15 (a rental property: kind real_estate_rental): the links and the scheme, all user-provided
    account: Optional[str] = None                   # E15-1: uid or label of the property's bank account (purpose rental)
    loan: Optional[str] = None                      # E15-1: id of the liability that financed it (else the loans whose `asset` is this id)
    commitment: Optional[Commitment] = None         # E15-3
    market_rate: Optional[MarketRate] = None        # E15-5
    vacancies: list[Vacancy] = Field(default_factory=list)   # E15-2
    notes: Optional[str] = None

    @field_validator("vacancies", mode="before")
    @classmethod
    def _vac(cls, v):
        return [] if v is None else v

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return _slug(v, "asset id")

    @field_validator("bank_match")
    @classmethod
    def _bm(cls, v):
        return _regex(v, "bank_match")

    @property
    def amount(self) -> Optional[float]:
        """The current value in EUR: ``balance`` for accounts, ``value`` for things (house, car)."""
        return self.balance if self.balance is not None else self.value


class AssetsFile(Base):
    assets: list[Asset] = Field(default_factory=list)

    @field_validator("assets", mode="before")
    @classmethod
    def _none(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _ids(self):
        _unique_ids(self.assets, "asset")
        return self


# ---------------------------------------------------------------- liabilities/*.yaml

class AmountMatch(Base):
    """E8: tells apart contracts that share one bank label (several policies of one insurer): a payment matches when its amount is within
    ``tolerance_pct`` of ``amount``."""
    amount: float = Field(gt=0)
    tolerance_pct: float = Field(default=3, ge=0, le=50)


class Rate(Base):
    type: Optional[Literal["fixed", "variable", "mixed"]] = None
    nominal: Optional[float] = Field(default=None, ge=0, le=60)
    taeg: Optional[float] = Field(default=None, ge=0, le=100)
    # E9-1, variable rate: STORED ONLY (the schedule uses `nominal` as the current rate and says so)
    index: Optional[str] = None                 # e.g. Euribor 12M, a reference text
    margin: Optional[float] = Field(default=None, ge=0, le=60)       # points over the index
    cap: Optional[float] = Field(default=None, ge=0, le=60)          # contractual ceiling of the rate (%)


class Insurance(Base):
    provider: Optional[str] = None
    monthly: Optional[float] = Field(default=None, ge=0)        # EUR per month (when the premium is a flat amount)
    delegated: Optional[bool] = None
    # E9-1: a premium quoted as a yearly percentage: of the INITIAL capital (flat) or of the capital still due (declining)
    rate_pct: Optional[float] = Field(default=None, ge=0, le=20)
    basis: Optional[Literal["initial", "outstanding"]] = None


class Deferral(Base):
    """E9-1: a deferral at the start of the loan. ``partial`` (franchise partielle): interest (and insurance) only; ``total``
    (franchise totale): nothing is paid and the interest is added to the capital."""
    months: int = Field(ge=1, le=120)
    kind: Literal["partial", "total"] = "partial"


class Odometer(Base):
    """E9-6: one mileage reading of a leased vehicle."""
    date: dt.date
    km: int = Field(ge=0)


class Liability(Base):
    id: str
    kind: Literal["mortgage", "car_loan", "loa", "lld", "consumer_loan", "bnpl"]
    lender: Optional[str] = None
    asset: Optional[str] = None
    holder: Optional[str] = None                # E9-4: household member id (or "joint") who owes it, for the per-owner net worth
    holders: Union[str, list[str], None] = None
    start_date: Optional[dt.date] = None
    end_date: Optional[dt.date] = None
    first_payment_date: Optional[dt.date] = None      # E9-1: due date of the first instalment (default: one month after start_date)
    term_months: Optional[int] = Field(default=None, ge=1, le=600)   # number of instalments / months (default: start_date -> end_date)
    payment_day: Optional[int] = Field(default=None, ge=1, le=31)    # day of the month the instalment is debited
    principal: Optional[float] = Field(default=None, ge=0)
    outstanding: Optional[float] = Field(default=None, ge=0)
    outstanding_as_of: Optional[dt.date] = None
    rate: Optional[Rate] = None
    monthly_payment: Optional[float] = Field(default=None, ge=0)     # total debited per month (LOA / LLD: the monthly rent)
    insurance: Optional[Insurance] = None
    deferral: Optional[Deferral] = None
    debited_from: Optional[str] = None
    debited_account: Optional[str] = None      # account uid or label the instalment is debited from (exact; used by the forecast)
    payment_match: Optional[str] = None
    amount_match: Optional[AmountMatch] = None   # E9-2: tells apart loans that share one bank label (like the contracts' one)
    early_repayment_penalty: Union[str, float, None] = None
    # LOA / LLD (E9-1, E9-6)
    first_payment: Optional[float] = Field(default=None, ge=0)       # first, increased rent / down payment (apport)
    residual_value: Optional[float] = Field(default=None, ge=0)      # purchase-option price at the end of the contract
    mileage_limit_km: Optional[int] = Field(default=None, ge=0)      # total km allowed over the whole contract
    excess_km_fee: Optional[float] = Field(default=None, ge=0)       # EUR per km above the limit
    initial_km: Optional[int] = Field(default=None, ge=0)            # odometer at the start of the contract (a new car: 0)
    odometer: list[Odometer] = Field(default_factory=list)           # readings the user records: date + km
    documents: list[str] = Field(default_factory=list)
    notes: Optional[str] = ""

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return _slug(v, "liability id")

    @field_validator("payment_match")
    @classmethod
    def _pm(cls, v):
        return _regex(v, "payment_match")

    @field_validator("documents", "odometer", mode="before")
    @classmethod
    def _docs(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _dates(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date is after end_date")
        if self.first_payment_date and self.start_date and self.first_payment_date < self.start_date:
            raise ValueError("first_payment_date is before start_date")
        if self.deferral and self.term_months and self.deferral.months >= self.term_months:
            raise ValueError("the deferral is not shorter than the term")
        km = [o.km for o in sorted(self.odometer, key=lambda o: o.date)]
        if any(b < a for a, b in zip(km, km[1:])):
            raise ValueError("odometer readings must not decrease over time")
        return self


# ---------------------------------------------------------------- contracts/*.yaml

class Billing(Base):
    amount: Optional[float] = None
    period: Optional[Literal["monthly", "bimonthly", "quarterly", "yearly"]] = None


class Cancellation(Base):
    method: Optional[str] = None
    legal_basis: Optional[str] = None


USAGE_FREQUENCIES = ("daily", "weekly", "monthly", "rarely", "never", "unknown")


class Usage(Base):
    """E8-2: how the household uses a service, as the USER states it (the bank data cannot know)."""
    frequency: Literal["daily", "weekly", "monthly", "rarely", "never", "unknown"] = "unknown"
    last_used: Optional[dt.date] = None
    note: Optional[str] = None

    @field_validator("last_used", mode="before")
    @classmethod
    def _lu(cls, v):
        return _iso_date(v)


class Contract(Base):
    id: str
    provider: Optional[str] = None
    kind: Optional[Literal["energy", "telecom", "insurance_home", "insurance_car", "insurance_health", "health",
                           "streaming", "software", "membership", "insurance_other", "water", "other"]] = None
    holder: Optional[str] = None                # E8-5: who signed it: a household member id or "joint" (letters); unknown -> a placeholder
    amount_match: Optional[AmountMatch] = None
    contract_number: Optional[str] = None       # E8-5: printed on cancellation letters (local only; masked for models)
    merchant_match: Optional[str] = None
    start_date: Optional[dt.date] = None
    renewal: Optional[dt.date] = None
    billing: Optional[Billing] = None
    commitment_end: Optional[dt.date] = None
    notice_period_days: Optional[int] = None
    cancellation: Optional[Cancellation] = None
    usage: Union[Usage, str, None] = None       # E8-2: {frequency, last_used, note}; a plain text note (older files) still loads
    keep: Union[bool, Literal["review"], None] = None
    documents: list[str] = Field(default_factory=list)
    notes: Optional[str] = ""

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return _slug(v, "contract id")

    @field_validator("merchant_match")
    @classmethod
    def _mm(cls, v):
        return _regex(v, "merchant_match")

    @field_validator("documents", mode="before")
    @classmethod
    def _docs(cls, v):
        return [] if v is None else v


# ---------------------------------------------------------------- household.yaml (E14-1)

class PocketMoney(Strict):
    """E14-5: the pocket money a child is MEANT to get (optional: the regular top-up is also detected from the payments)."""
    amount: float
    period: Literal["weekly", "monthly"]
    day: Optional[int] = Field(default=None, ge=1, le=31)      # day of the month (monthly) or ISO weekday 1-7 (weekly)

    @field_validator("amount")
    @classmethod
    def _amount(cls, v):
        if isinstance(v, bool) or v <= 0:
            raise ValueError("pocket money amount must be a positive amount in EUR")
        return v


class Member(Base):
    id: str
    name: str                                   # display name: stays on this machine
    role: Literal["adult", "child"]
    birth_year: Optional[int] = Field(default=None, ge=1900, le=2100)
    aliases: list[str] = Field(default_factory=list)    # holder-name spellings seen in bank data, local only
    pocket_money: Optional[PocketMoney] = None          # E14-5, children only (checked by `memory check`)

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return _slug(v, "member id")

    @field_validator("aliases", mode="before")
    @classmethod
    def _al(cls, v):
        return [] if v is None else v


class AttributionMatch(Strict):
    """E14-3: what a transaction must look like to be attributed to a member by a rule. Every key given must match (AND)."""
    account: Optional[str] = None               # account uid or label
    card_last4: Optional[str] = None            # the last four digits of the card, when the bank prints them in the description
    merchant_key: Optional[str] = None          # regex on the merchant key
    description: Optional[str] = None           # regex on the bank description
    direction: Optional[Literal["in", "out"]] = None
    amount_min: Optional[float] = Field(default=None, ge=0)      # magnitudes in EUR
    amount_max: Optional[float] = Field(default=None, ge=0)

    @field_validator("card_last4")
    @classmethod
    def _l4(cls, v):
        if v is not None and not re.fullmatch(r"\d{4}", str(v)):
            raise ValueError("card_last4 must be exactly four digits (quote it: '0123')")
        return v

    @field_validator("merchant_key")
    @classmethod
    def _mk(cls, v):
        return _regex(v, "match.merchant_key")

    @field_validator("description")
    @classmethod
    def _desc(cls, v):
        return _regex(v, "match.description")

    @model_validator(mode="after")
    def _some(self):
        if not any(v is not None for v in (self.account, self.card_last4, self.merchant_key, self.description, self.direction,
                                           self.amount_min, self.amount_max)):
            raise ValueError("an attribution rule needs at least one condition (account, card_last4, merchant_key, description, direction, amount_min, amount_max)")
        if self.direction is not None and not any(v is not None for v in (self.account, self.card_last4, self.merchant_key, self.description)):
            raise ValueError("a direction or an amount alone would attribute every such transaction: add an account, a card, a merchant or a description")
        return self


class AttributionRule(Strict):
    """E14-3: transactions that match are attributed to `member`. Rules are tried in file order; the first match wins; a manual
    reassignment (`coach household assign`) outranks every rule; with no rule the account's owner decides."""
    id: str
    member: str                                 # a member id, or 'joint' (= the household as a whole)
    match: AttributionMatch
    note: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return _slug(v, "attribution rule id")


class KidBudget(Strict):
    """E14-6: a weekly or monthly spending limit of one member (a child), optionally for one category or group."""
    id: str
    member: str
    period: Literal["weekly", "monthly"]
    limit: float
    category: Optional[str] = None
    group: Optional[str] = None
    note: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return _slug(v, "kid budget id")

    @field_validator("limit")
    @classmethod
    def _limit(cls, v):
        if isinstance(v, bool) or v <= 0:
            raise ValueError("limit must be a positive amount in EUR")
        return v

    @model_validator(mode="after")
    def _one(self):
        if self.category and self.group:
            raise ValueError("a kid budget has at most one of category or group")
        if self.category and not re.match(r"^[a-z0-9_]+\.[a-z0-9_]+$", self.category):
            raise ValueError(f"category must look like group.leaf (got {self.category!r})")
        return self


class AllocationMatch(Strict):
    category: Optional[str] = None
    group: Optional[str] = None
    tag: Optional[str] = None
    merchant_key: Optional[str] = None
    account: Optional[str] = None

    @field_validator("merchant_key")
    @classmethod
    def _mk(cls, v):
        return _regex(v, "match.merchant_key")

    @model_validator(mode="after")
    def _some(self):
        if not any((self.category, self.group, self.tag, self.merchant_key, self.account)):
            raise ValueError("an allocation needs at least one of category, group, tag, merchant_key, account")
        return self


class Allocation(Strict):
    """E14-9: a shared cost split between members by a rule. `equal`: the same share for each of `among`; `income`: in proportion
    to each member's income over the last 12 closed months; `custom`: `shares` (percent, adding up to 100)."""
    id: str
    title: Optional[str] = None
    match: AllocationMatch
    method: Literal["equal", "income", "custom"] = "equal"
    among: list[str] = Field(default_factory=list)       # members sharing (default: every adult)
    shares: dict[str, float] = Field(default_factory=dict)
    note: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return _slug(v, "allocation id")

    @field_validator("among", mode="before")
    @classmethod
    def _among(cls, v):
        return [] if v is None else v

    @field_validator("shares", mode="before")
    @classmethod
    def _shares(cls, v):
        return {} if v is None else v

    @model_validator(mode="after")
    def _check(self):
        if self.method == "custom":
            if not self.shares:
                raise ValueError("a custom allocation needs shares (percent per member, adding up to 100)")
            if any(isinstance(x, bool) or x < 0 for x in self.shares.values()):
                raise ValueError("shares must be percentages >= 0")
            if abs(sum(self.shares.values()) - 100) > 0.01:
                raise ValueError(f"shares must add up to 100 (got {sum(self.shares.values()):g})")
        elif self.shares:
            raise ValueError("shares only make sense with method: custom")
        if self.method != "custom" and self.among and len(set(self.among)) < 2:
            raise ValueError("among needs at least two members (or leave it empty for every adult)")
        return self


class Contact(Base):
    """E8-5: the holder's postal address and e-mail, used ONLY to fill cancellation letters on this machine. Never sent to a
    model: the privacy guard and the redactors know these values, no tool or context reads the block."""
    address: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class HouseholdFile(Base):
    members: list[Member] = Field(default_factory=list)
    employers: list[str] = Field(default_factory=list)   # replaced by [employer] in `memory context --coarse`
    places: list[str] = Field(default_factory=list)      # towns / neighbourhoods: replaced by [place] with --coarse
    schools: list[str] = Field(default_factory=list)     # schools / nurseries: masked by the analytics redaction ([school])
    country: Optional[Literal["FR", "IT"]] = None        # tax / cancellation rules of the coach skills (FR assumed when absent)
    contact: Optional[Contact] = None                    # E8-5: local only (letters); never in any model-facing output
    attribution: list[AttributionRule] = Field(default_factory=list)   # E14-3: rules attributing transactions to members
    kid_budgets: list[KidBudget] = Field(default_factory=list)         # E14-6: weekly / monthly limits
    allocations: list[Allocation] = Field(default_factory=list)        # E14-9: shared cost allocation rules

    @field_validator("attribution", "kid_budgets", "allocations", mode="before")
    @classmethod
    def _lists2(cls, v):
        return [] if v is None else v

    @field_validator("employers", "places", "schools", mode="before")
    @classmethod
    def _lists(cls, v):
        return [] if v is None else v

    @field_validator("members", mode="before")
    @classmethod
    def _none(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _ids(self):
        _unique_ids(self.members, "member")
        _unique_ids(self.attribution, "attribution rule")
        _unique_ids(self.kid_budgets, "kid budget")
        _unique_ids(self.allocations, "allocation")
        return self


# ---------------------------------------------------------------- events.yaml (optional structured index of events.md)

class Event(Base):
    id: str
    title: Optional[str] = None
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None
    budget: Optional[float] = None
    status: Optional[Literal["planned", "ongoing", "done"]] = None
    note: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return _slug(v, "event id")


class EventsFile(Base):
    events: list[Event] = Field(default_factory=list)

    @field_validator("events", mode="before")
    @classmethod
    def _none(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _ids(self):
        _unique_ids(self.events, "event")
        return self


# ---------------------------------------------------------------- open-questions.yaml (E3-3)

class SuggestedTarget(Strict):
    file: str
    field: Optional[str] = None


class TextMsg(Strict):
    """A sentence the web app translates (docs/i18n.md "Server text"): ``code`` (a key of the web's ``server`` namespace), RAW params typed
    by their name (``coach.i18n_msg``), and ``text`` (the English, shown when the web does not know the code)."""
    code: str
    params: dict[str, Any] = Field(default_factory=dict)
    text: str

    @model_validator(mode="before")
    @classmethod
    def _check(cls, v):
        from coach.i18n_msg import MessageError, server_msg
        if not isinstance(v, dict):
            raise ValueError("a message is a mapping {code, params, text}")
        extra = sorted(set(v) - {"code", "params", "text"})
        if extra:
            raise ValueError(f"unknown key(s) of a message: {', '.join(map(str, extra))}")
        params = v.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("params must be a mapping")
        # YAML reads an unquoted date as a date: a param keeps its ISO text whatever its name
        params = {k: (x.isoformat() if isinstance(x, dt.date) else x) for k, x in params.items()}
        try:
            return server_msg(v.get("code") or "", v.get("text") or "", **params)
        except (MessageError, TypeError) as e:
            raise ValueError(str(e)) from None


class Question(Strict):
    id: str
    status: Literal["open", "answered", "dismissed"] = "open"
    topic: str = "General"
    topic_code: Optional[str] = None                            # generated questions: the topic's code (the web's labels.questionTopic.<code>)
    question: str
    question_msg: Optional[TextMsg] = None                      # generated questions: the web's translation of `question` (same English text)
    context: Optional[str] = None
    context_msg: Optional[TextMsg] = None
    evidence: dict[str, Any] = Field(default_factory=dict)     # amounts / counts / dates, never raw descriptions
    suggested_target: Optional[SuggestedTarget] = None
    created: Optional[dt.date] = None
    answered: Optional[dt.date] = None
    answer: Optional[str] = None
    note: Optional[str] = None                                  # dismissal reason
    key: Optional[str] = None                                   # dedupe key of generated questions
    origin: Literal["manual", "generated", "migrated", "coach"] = "manual"
    stake: Optional[float] = None                               # money at stake (EUR, absolute), for ordering
    source_text: Optional[str] = None                           # migrated items: the original line, verbatim

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", v):
            raise ValueError(f"question id must be lowercase letters, digits, '-' '_' (got {v!r})")
        return v

    @field_validator("topic_code")
    @classmethod
    def _topic_code(cls, v):
        if v is not None and not re.match(r"^[a-z][a-zA-Z0-9]*$", v):
            raise ValueError(f"topic_code must be lower camel case (got {v!r})")
        return v

    @field_validator("evidence")
    @classmethod
    def _ev(cls, v):
        def ok(x):
            return x is None or isinstance(x, (str, int, float, bool, dt.date)) or \
                (isinstance(x, list) and all(ok(i) for i in x))
        for k, x in v.items():
            if not ok(x):
                raise ValueError(f"evidence[{k!r}] must be a scalar or a list of scalars")
        return v

    @model_validator(mode="after")
    def _consistent(self):
        if self.status == "answered" and not (self.answer or "").strip():
            raise ValueError("an answered question needs an answer text")
        if self.status != "answered" and self.answer:
            raise ValueError("only an answered question can carry an answer (reopen it first)")
        # a message translates its English sibling: a hand-edited question (or context) keeps its own words, the stale message is dropped
        if self.question_msg is not None and self.question_msg.text != self.question:
            self.question_msg = None
        if self.context_msg is not None and self.context_msg.text != self.context:
            self.context_msg = None
        return self


class QuestionsFile(Base):
    questions: list[Question] = Field(default_factory=list)

    @field_validator("questions", mode="before")
    @classmethod
    def _none(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _ids(self):
        _unique_ids(self.questions, "question")
        return self


# ---------------------------------------------------------------- documents.yaml (E3-5)

class Document(Base):
    id: str
    sha256: str
    filename: str
    stored_as: str
    kind: Literal["loan", "contract", "insurance", "statement", "other"]
    size: int
    added: dt.date
    for_id: Optional[str] = Field(default=None, alias="for")
    mime: Optional[str] = None

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DocumentsFile(Base):
    documents: list[Document] = Field(default_factory=list)

    @field_validator("documents", mode="before")
    @classmethod
    def _none(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _ids(self):
        _unique_ids(self.documents, "document")
        return self


# ---------------------------------------------------------------- budgets.yaml (E4-7)

class Budget(Base):
    """A monthly envelope for one category (``category: food.groceries``) or a whole taxonomy group
    (``group: food``). ``owner`` / ``account`` narrow it to the accounts of one owner / one account (uid or label)."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str
    category: Optional[str] = None
    group: Optional[str] = None
    monthly: float
    rollover: bool = False
    owner: Optional[str] = None
    account: Optional[str] = None
    start: Optional[dt.date] = None
    note: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return _slug(v, "budget id")

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v is not None and not re.match(r"^[a-z0-9_]+\.[a-z0-9_]+$", v):
            raise ValueError(f"category must look like group.leaf (got {v!r})")
        return v

    @field_validator("group")
    @classmethod
    def _grp(cls, v):
        if v is not None and not re.match(r"^[a-z0-9_]+$", v):
            raise ValueError(f"group must be a taxonomy group such as 'food' (got {v!r})")
        return v

    @field_validator("monthly")
    @classmethod
    def _monthly(cls, v):
        if isinstance(v, bool) or v <= 0:
            raise ValueError("monthly must be a positive amount in EUR")
        return v

    @model_validator(mode="after")
    def _one_target(self):
        if bool(self.category) == bool(self.group):
            raise ValueError("a budget needs exactly one of category (group.leaf) or group")
        return self


class BudgetsFile(Base):
    budgets: list[Budget] = Field(default_factory=list)

    @field_validator("budgets", mode="before")
    @classmethod
    def _none(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _ids(self):
        _unique_ids(self.budgets, "budget")
        return self


# ---------------------------------------------------------------- goals.yaml (E4-9)

class Goal(Base):
    """A savings goal. Progress comes from ONE source: an asset of assets.yaml (``asset``), an account (``account``:
    uid or label) or the flows carrying a tag (``tag``, e.g. savings). ``baseline`` is the amount already put aside
    on ``start`` for a tag-based goal."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str
    title: Optional[str] = None
    target_amount: float
    target_date: Optional[dt.date] = None
    asset: Optional[str] = None
    account: Optional[str] = None
    tag: Optional[str] = None
    monthly_contribution: Optional[float] = None
    baseline: Optional[float] = None
    start: Optional[dt.date] = None
    note: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return _slug(v, "goal id")

    @field_validator("target_amount")
    @classmethod
    def _target(cls, v):
        if isinstance(v, bool) or v <= 0:
            raise ValueError("target_amount must be a positive amount in EUR")
        return v

    @field_validator("monthly_contribution")
    @classmethod
    def _contrib(cls, v):
        if v is not None and (isinstance(v, bool) or v < 0):
            raise ValueError("monthly_contribution must be >= 0")
        return v

    @model_validator(mode="after")
    def _one_source(self):
        n = sum(1 for x in (self.asset, self.account, self.tag) if x)
        if n != 1:
            raise ValueError("a goal is linked to exactly one of asset, account or tag")
        return self


class GoalsFile(Base):
    goals: list[Goal] = Field(default_factory=list)

    @field_validator("goals", mode="before")
    @classmethod
    def _none(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _ids(self):
        _unique_ids(self.goals, "goal")
        return self
