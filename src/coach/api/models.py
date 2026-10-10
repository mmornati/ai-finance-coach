"""Response models. Analytics results are the ``to_dict()`` structures documented in docs/analytics.md and are
passed through as JSON objects (``AnalyticsJson``); the models below describe the shapes the web layer adds."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

Money = str      # decimal string with two decimals, e.g. "-12.34": never a float


class AnalyticsJson(BaseModel):
    """A ``to_dict()`` result of :mod:`coach.analytics` (money as strings, ``coverage`` and ``evidence`` included)."""
    model_config = ConfigDict(extra="allow")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class SessionInfo(BaseModel):
    csrf_token: str
    version: str
    today: str
    locale: str = "fr-FR"
    allow_remote: bool
    coach: dict
    sync_daily_limit: int
    enable_banking_configured: bool
    memory_history: bool
    user: Optional[dict] = None             # E14-8: who this session is (id, role, member_id, prefs)
    auth: dict = {}                         # E16: the ways into a session: {"passkeys": bool, "sso": bool, "sso_sign_out": str | None}


class Page(BaseModel):
    limit: int
    offset: int
    total: int
    next_offset: Optional[int] = None


class TxOut(BaseModel):
    tx_key: str
    date: str
    amount: Money
    category: str
    source: str
    tags: list[str]
    event: Optional[str]
    entity: str
    merchant: Optional[str]
    description: str
    account: str
    account_label: str
    owner: Optional[str]
    purpose: Optional[str]
    type: Optional[str]
    split: bool
    overridden: bool
    transfer_linked: bool
    person: Optional[str] = None            # E14-3: the household member it is attributed to (an id), 'joint', or None


class TxTotals(BaseModel):
    count: int
    sum: Money
    income: Money
    outflow: Money


class TxList(Page):
    items: list[TxOut]
    totals: TxTotals


class CategoryChange(BaseModel):
    tx_key: str
    category: str
    scope: str = Field(pattern="^(transaction|merchant|memory)$")
    note: Optional[str] = None
    confirm_scope: bool = False
    tags: list[str] = []
    event: Optional[str] = None
    match: str = Field("merchant", pattern="^(merchant|transaction)$",
                       description="memory scope: annotate the merchant key or only this transaction")


class AnnotationRequest(BaseModel):
    tx_keys: list[str] = []
    merchant_key: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = []
    event: Optional[str] = None
    note: Optional[str] = None
    id: Optional[str] = None
    reason: Optional[str] = None


class Preview(BaseModel):
    changed: bool
    diff: str
    summary: dict = {}
    warnings: list[str] = []


class Written(BaseModel):
    changed: bool
    change_id: Optional[str] = None
    diff: str = ""
