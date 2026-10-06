"""Connections (E5-14): accounts, consents, health, sync now, connect / reconnect. IBANs are masked, nothing secret
is ever returned (no tokens, no keys, no Enable Banking credentials)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from coach.api.deps import get_state
from coach.api.errors import ApiError
from coach.api.jobs import Busy
from coach.api.state import AppState
from coach.config import ConfigError
from coach.ingest import accounts as accounts_mod, auth, consent as consent_mod, health as health_mod
from coach.ingest.client import ApiError as EbApiError, EnableBankingClient

router = APIRouter(tags=["connections"])


def _accounts(con) -> list[dict]:
    rows = accounts_mod.list_accounts(con)
    out = []
    for r in rows:
        out.append({"uid": r["uid"], "bank": r["bank"], "label": r["label"], "name": r["name"],
                    "iban_last4": accounts_mod.mask_iban(r["iban"]), "currency": r["currency"], "owner": r["owner"],
                    "purpose": r["purpose"], "excluded": bool(r["exclude"]), "source": r["source"],
                    "session_status": r["session_status"], "needs_review": bool(r["needs_review"]), "tx_count": r["tx_count"]})
    return out


@router.get("/connections", summary="Accounts, consents (days left), connector health and the sync state")
def connections(state: AppState = Depends(get_state)):
    cfg = state.cfg
    with state.read() as con:
        accs = _accounts(con)
        consents = [{"session_id": c.session_id, "bank": c.bank, "country": c.country, "valid_until": c.valid_until,
                     "days_left": c.days_left, "status": c.status, "live_status": c.live_status,
                     "live_checked_at": c.live_checked_at, "accounts": c.accounts}
                    for c in consent_mod.list_consents(con)]
        rep = health_mod.health(con, cfg.sync_daily_limit, cfg.health_stale_days).to_dict()
    try:                                                       # E12: the last scheduled run (step names, durations, counts), on the health card
        from coach.quality import logs as run_logs
        rep["last_run"] = run_logs.last_run(cfg.log_dir)
    except Exception:                                          # noqa: BLE001
        rep["last_run"] = None
    left = {a["uid"]: a["syncs_left_today"] for b in rep["banks"] for a in b["accounts"] if a["source"] == "api"}
    for a in accs:
        a["syncs_left_today"] = left.get(a["uid"])
    return {"accounts": accs, "consents": consents, "health": rep, "purposes": list(accounts_mod.PURPOSES),
            "sync": {"daily_limit": cfg.sync_daily_limit, **state.jobs.sync_job.to_dict()},
            "enable_banking_configured": bool(cfg.eb_app_id and cfg.eb_private_key_path and cfg.eb_redirect_url),
            "connect": state.jobs.auth_job.to_dict()}


class AccountPatch(BaseModel):
    label: Optional[str] = Field(None, max_length=80)
    owner: Optional[str] = Field(None, max_length=60)
    purpose: Optional[str] = None
    exclude: Optional[bool] = None
    resolve: bool = False


@router.patch("/accounts/{uid:path}", summary="Edit an account: label, owner, purpose, exclusion from analytics")
def patch_account(uid: str, req: AccountPatch, state: AppState = Depends(get_state)):
    from coach.household import people as people_mod
    people = people_mod.load(state.store)
    with state.write() as con:
        real = accounts_mod.set_account(con, uid, label=req.label, owner=req.owner, purpose=req.purpose,
                                        exclude=req.exclude, resolve=req.resolve, people=people)
        row = next(a for a in _accounts(con) if a["uid"] == real)
    return row


# ---------------------------------------------------------------- sync

class SyncRequest(BaseModel):
    account: Optional[str] = None


@router.post("/sync", summary="Sync now (respects the daily limit per account; runs in the background)")
def sync(req: Optional[SyncRequest] = None, state: AppState = Depends(get_state)):
    cfg = state.cfg
    if not (cfg.eb_app_id and cfg.eb_private_key_path):
        raise ApiError(409, "not_configured", "Enable Banking is not configured: see [enable_banking] in config.toml")
    try:
        return state.jobs.start_sync(req.account if req else None).to_dict()
    except Busy as e:
        raise ApiError(409, "busy", str(e))
    except ConfigError as e:
        raise ApiError(409, "not_configured", str(e))


@router.get("/sync/status", summary="State of the last / running sync")
def sync_status(state: AppState = Depends(get_state)):
    return state.jobs.sync_job.to_dict()


# ---------------------------------------------------------------- connect / reconnect

class ConnectRequest(BaseModel):
    bank: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=2, max_length=2)
    days: int = Field(180, ge=1, le=730)
    replace: bool = False


class ReconnectRequest(BaseModel):
    session_id: str
    days: int = Field(180, ge=1, le=730)


def _start(state: AppState, **kw):
    try:
        return state.jobs.start_connect(**kw).to_dict()
    except Busy as e:
        raise ApiError(409, "busy", str(e))
    except ConfigError as e:
        raise ApiError(409, "not_configured", str(e))


@router.post("/connections/connect", summary="Start a bank connection: returns once the bank's login page is ready (poll /connections/auth/status)")
def connect(req: ConnectRequest, state: AppState = Depends(get_state)):
    return _start(state, bank=req.bank, country=req.country.upper(), days=req.days, replace=req.replace)


@router.post("/connections/reconnect", summary="Renew a consent for the same bank (the old session is retired when the new one completes)")
def reconnect(req: ReconnectRequest, state: AppState = Depends(get_state)):
    bank, country, sid = state.jobs.reconnect_args(req.session_id)
    return _start(state, bank=bank, country=country, days=req.days, replaces=sid)


@router.get("/connections/auth/status", summary="State of the running authorisation: waiting (with the bank URL), done, failed")
def auth_status(state: AppState = Depends(get_state)):
    return state.jobs.auth_job.to_dict()


@router.get("/banks", summary="Banks available for a country (asks Enable Banking)")
def banks(country: str = Query(..., min_length=2, max_length=2), q: Optional[str] = None, state: AppState = Depends(get_state)):
    try:
        res = EnableBankingClient.from_config(state.cfg).call("GET", "/aspsps", params={"country": country.upper(), "psu_type": "personal"})
    except ConfigError as e:
        raise ApiError(409, "not_configured", str(e))
    except EbApiError as e:
        raise ApiError(502, "bank_api_error", f"Enable Banking answered with an error (status {e.status})")
    items = res.get("aspsps", res) if isinstance(res, dict) else res
    out = [{"name": b["name"], "country": b["country"], "max_consent_days": (b.get("maximum_consent_validity") or 0) // 86400,
            "beta": bool(b.get("beta"))} for b in items if not q or q.lower() in b["name"].lower()]
    return {"banks": out[:200]}
