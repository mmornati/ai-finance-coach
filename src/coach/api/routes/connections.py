"""Connections (E5-14): accounts, consents, health, sync now, connect / reconnect. IBANs are masked, nothing secret
is ever returned (no tokens, no keys, no Enable Banking credentials)."""
from __future__ import annotations

import base64
import tempfile
import uuid
from pathlib import Path, PurePath
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
from coach.ingest.imports import core as import_core, parsers as import_parsers, profiles as import_profiles

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


# ---------------------------------------------------------------- statement imports

class ImportPreviewRequest(BaseModel):
    file_name: str = Field(..., min_length=1)
    content_base64: str = Field(..., min_length=1)
    account: Optional[str] = None
    profile: Optional[str] = None
    bank: Optional[str] = None
    currency: Optional[str] = None
    force: bool = False


class ImportExecuteRequest(BaseModel):
    file_name: str = Field(..., min_length=1)
    content_base64: str = Field(..., min_length=1)
    account: str = Field(..., min_length=1)
    profile: Optional[str] = None
    bank: Optional[str] = None
    currency: Optional[str] = None
    force: bool = False


MAX_UPLOAD_BASE64_LEN = 20 * 1024 * 1024  # 20 MB base64 string
MAX_UPLOAD_BYTES = 10 * 1024 * 1024       # 10 MB raw binary data
ALLOWED_UPLOAD_SUFFIXES = {".pdf", ".csv", ".ofx", ".qfx", ".xml"}


def _decode_and_validate_upload(b64_str: str) -> bytes:
    if len(b64_str) > MAX_UPLOAD_BASE64_LEN:
        raise ApiError(400, "file_too_large", f"File upload exceeds base64 length limit ({MAX_UPLOAD_BASE64_LEN // (1024 * 1024)} MB)")
    try:
        data = base64.b64decode(b64_str, validate=True)
    except Exception as e:
        raise ApiError(400, "invalid_base64", f"Could not decode base64 file content: {e}")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(400, "file_too_large", f"File upload exceeds maximum allowed size ({MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
    return data


def _write_safe_temp_upload(tmpdir: Path | str, client_filename: str, data: bytes) -> Path:
    raw_suffix = Path(client_filename).suffix.lower()
    suffix = raw_suffix if raw_suffix in ALLOWED_UPLOAD_SUFFIXES else ".bin"
    safe_name = f"upload_{uuid.uuid4().hex[:12]}{suffix}"
    dest = (Path(tmpdir) / safe_name).resolve()
    base_dir = Path(tmpdir).resolve()
    if not dest.is_relative_to(base_dir) or dest.parent != base_dir:
        raise ApiError(400, "invalid_filename", "Invalid upload filename")
    dest.write_bytes(data)
    return dest


@router.get("/connections/import/profiles", summary="List available CSV import profiles")
def list_import_profiles(state: AppState = Depends(get_state)):
    profiles = [{"name": name, "description": desc} for name, desc in import_profiles.list_profiles(state.cfg.import_profiles_dir)]
    return {"profiles": profiles}


@router.post("/connections/import/preview", summary="Preview statement import (dedup check, format detection, date range)")
def preview_import(req: ImportPreviewRequest, state: AppState = Depends(get_state)):
    data = _decode_and_validate_upload(req.content_base64)
    clean_client_filename = PurePath(req.file_name).name or "statement"

    prof = None
    if req.profile:
        try:
            prof = import_profiles.load_profile(state.cfg.import_profiles_dir, req.profile)
        except import_profiles.ProfileError as e:
            raise ApiError(400, "invalid_profile", str(e))

    try:
        fmt = import_parsers.detect_format(req.file_name, data)
    except import_parsers.ParseError as e:
        raise ApiError(400, "unsupported_format", str(e))

    if fmt == "csv" and not prof:
        raise ApiError(400, "profile_required", "CSV statements require an import profile. Please select a profile.")

    with state.write(quiet=True) as con:
        try:
            if fmt == "ofx":
                parsed = import_parsers.parse_ofx(data)
            elif fmt == "camt053":
                parsed = import_parsers.parse_camt053(data)
            elif fmt == "pdf":
                parsed = import_parsers.parse_pdf(data, prof, bank=req.bank)
            elif fmt == "csv":
                parsed = import_parsers.parse_csv(data, prof)
            else:
                raise ApiError(400, "unsupported_format", f"Unknown format: {fmt}")
        except (import_parsers.ParseError, import_profiles.ProfileError) as e:
            raise ApiError(400, "parse_error", str(e))

        suggested_account_uid = None
        if parsed.iban:
            norm_iban = accounts_mod.normalize_iban(parsed.iban)
            h = accounts_mod.ident_hash(norm_iban)
            acc = con.execute("SELECT uid FROM accounts WHERE identification_hash=?", (h,)).fetchone()
            if acc:
                suggested_account_uid = acc[0]

        target_account = req.account or suggested_account_uid

        if target_account:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmppath = _write_safe_temp_upload(tmpdir, clean_client_filename, data)
                try:
                    rep = import_core.import_file(
                        con, tmppath, account=target_account, profile=prof,
                        fmt=fmt, dry_run=True, bank=req.bank,
                        currency_filter=req.currency, force=req.force
                    )
                except import_core.ImportFailed as e:
                    raise ApiError(400, "import_failed", str(e))

                rows_to_sample = rep.new_rows if rep.new_rows else parsed.rows
                sample = [
                    {
                        "date": r.date,
                        "amount": float(r.amount),
                        "currency": r.currency,
                        "description": r.description,
                        "counterparty": r.counterparty
                    }
                    for r in rows_to_sample[:15]
                ]
                return {
                    "file_name": clean_client_filename,
                    "format": rep.format,
                    "iban": parsed.iban,
                    "suggested_account_uid": suggested_account_uid,
                    "account_uid": rep.account_uid,
                    "account_created": rep.account_created,
                    "date_min": rep.date_min,
                    "date_max": rep.date_max,
                    "rows_total": rep.rows_total,
                    "rows_new": rep.rows_new,
                    "rows_duplicate": rep.rows_duplicate,
                    "rows_skipped": rep.rows_skipped,
                    "already_imported": rep.already_imported,
                    "sample_rows": sample,
                    "warnings": rep.warnings,
                }

        rows = parsed.rows
        sample = [
            {
                "date": r.date,
                "amount": float(r.amount),
                "currency": r.currency,
                "description": r.description,
                "counterparty": r.counterparty
            }
            for r in rows[:15]
        ]
        return {
            "file_name": clean_client_filename,
            "format": parsed.format,
            "iban": parsed.iban,
            "suggested_account_uid": None,
            "account_uid": None,
            "account_created": False,
            "date_min": min((r.date for r in rows), default=None),
            "date_max": max((r.date for r in rows), default=None),
            "rows_total": len(rows),
            "rows_new": len(rows),
            "rows_duplicate": 0,
            "rows_skipped": 0,
            "already_imported": None,
            "sample_rows": sample,
            "warnings": list(parsed.warnings),
        }


@router.post("/connections/import/execute", summary="Execute statement file import")
def execute_import(req: ImportExecuteRequest, state: AppState = Depends(get_state)):
    data = _decode_and_validate_upload(req.content_base64)
    clean_client_filename = PurePath(req.file_name).name or "statement"

    prof = None
    if req.profile:
        try:
            prof = import_profiles.load_profile(state.cfg.import_profiles_dir, req.profile)
        except import_profiles.ProfileError as e:
            raise ApiError(400, "invalid_profile", str(e))

    with state.write() as con:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = _write_safe_temp_upload(tmpdir, clean_client_filename, data)
            try:
                rep = import_core.import_file(
                    con, tmppath, account=req.account, profile=prof,
                    dry_run=False, bank=req.bank, currency_filter=req.currency, force=req.force
                )
            except (import_core.ImportFailed, import_parsers.ParseError, import_profiles.ProfileError) as e:
                raise ApiError(400, "import_failed", str(e))

    return {
        "file": clean_client_filename,
        "format": rep.format,
        "account_uid": rep.account_uid,
        "account_created": rep.account_created,
        "rows_total": rep.rows_total,
        "rows_new": rep.rows_new,
        "rows_duplicate": rep.rows_duplicate,
        "rows_skipped": rep.rows_skipped,
        "date_min": rep.date_min,
        "date_max": rep.date_max,
        "already_imported": rep.already_imported,
        "warnings": rep.warnings,
    }
