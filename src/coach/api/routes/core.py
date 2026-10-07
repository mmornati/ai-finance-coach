"""Session, metadata, health, coverage, balances."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from coach import __version__
from coach.analytics.common import money_str
from coach.api import views
from coach.api.deps import ScopeParams, get_member_snapshot, get_snapshot, get_state
from coach.api.models import AnalyticsJson, SessionInfo
from coach.api.state import AppState, Snapshot
from coach.ingest import health as health_mod

router = APIRouter()


class ExchangeRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


@router.post("/session/exchange", tags=["core"], summary="Trade the one-time login token for the session cookie")
def exchange(req: ExchangeRequest, request: Request):
    """The only call that needs no session. The token (printed by `coach ui` / `coach ui --login-link`, carried in the
    URL fragment) is single-use and short-lived; attempts are rate limited."""
    from coach.api.app import set_session_cookie
    from coach.api.errors import ApiError
    sec = request.app.state.security
    if not sec.exchange_bucket.take("login"):
        raise ApiError(429, "rate_limited", "too many login attempts: wait a few minutes")
    bad = ApiError(401, "invalid_login_token", "this login link is invalid, already used or expired: run `coach ui --login-link` "
                                               "in your terminal for a fresh one")
    if not sec.tokens:
        raise bad
    ok, login = sec.tokens.consume_user(req.token)
    if not ok:
        raise bad
    if login:                                                  # E14-8: a link made for one login; it must still exist and be enabled
        from coach.household import users as users_mod
        with request.app.state.coach.read() as con:
            u = users_mod.get(con, login)
        if u is None or not u.active:
            raise bad
    resp = JSONResponse({"ok": True})
    set_session_cookie(request.app, resp, sec.new_cookie(user=login))
    return resp


@router.post("/session/logout", tags=["core"], summary="End this browser session (the cookie is revoked server-side)")
def logout(request: Request):
    sec = request.app.state.security
    sec.revoke(request.state.cookie)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(sec.cookie_name, path="/")
    return resp


@router.get("/session", response_model=SessionInfo, tags=["core"], summary="Session info and the CSRF token")
def session(request: Request, state: AppState = Depends(get_state)):
    sec = request.app.state.security
    cfg = state.cfg
    user = getattr(request.state, "user", None)
    if user is not None and user.is_child:                  # a child gets the session and nothing about the setup (coach, banks, history)
        return SessionInfo(csrf_token=sec.csrf_token(request.state.cookie), version=__version__, user=user.to_dict(),
                           today=state.clock().isoformat(), allow_remote=False, coach={"configured": False, "backend": "", "message": ""},
                           sync_daily_limit=0, enable_banking_configured=False, memory_history=False)
    from coach.api.coachjobs import availability
    coach_ok, coach_msg = availability(cfg)
    return SessionInfo(csrf_token=sec.csrf_token(request.state.cookie), version=__version__,
                       user=user.to_dict() if user is not None else None,
                       today=state.clock().isoformat(), allow_remote=cfg.ui_allow_remote,
                       coach={"configured": coach_ok, "backend": cfg.coach_backend,
                              "message": coach_msg or f"The coach answers through {cfg.coach_backend}."},
                       sync_daily_limit=cfg.sync_daily_limit,
                       enable_banking_configured=bool(cfg.eb_app_id and cfg.eb_private_key_path and cfg.eb_redirect_url),
                       memory_history=cfg.memory_history)


@router.get("/meta/taxonomy", tags=["core"], summary="Category taxonomy (groups and leaves)")
def taxonomy(snap: Snapshot = Depends(get_snapshot)):
    return views.taxonomy()


WEB_DISCLAIMERS = ("ai_label", "ai_label_short")          # the disclaimers the web app shows itself (its locale files never copy them)


@router.get("/meta/disclaimers", tags=["core"], summary="The legal labels the web app shows, in one language")
def meta_disclaimers(lang: str = "en"):
    """The wording lives only in ``coach/disclaimers.py``: the web asks for it in its interface language (English for an unknown one)."""
    from coach import disclaimers
    code = (lang or "en").lower()[:2]
    code = code if code in disclaimers.LANGS else "en"
    return {"lang": code, "texts": {k: disclaimers.get(k, code) for k in WEB_DISCLAIMERS}}


@router.get("/meta/filters", tags=["core"], summary="Values for every filter: accounts, owners, purposes, tags, events")
def filters(snap: Snapshot = Depends(get_snapshot), state: AppState = Depends(get_state)):
    with state.read() as con:
        return views.filters_meta(snap.ds, con, state.store)


@router.get("/health", tags=["core"], summary="Connector health per bank and account, plus the memory check summary")
def health(state: AppState = Depends(get_state)):
    cfg = state.cfg
    with state.read() as con:
        rep = health_mod.health(con, cfg.sync_daily_limit, cfg.health_stale_days).to_dict()
    try:
        rep["memory"] = state.memory_check()[1]
    except Exception as e:                                                  # noqa: BLE001 - a health summary must not crash
        rep["memory"] = {"errors": 0, "warnings": 0, "info": 0, "by_code": {}, "failed": type(e).__name__}
    try:                                                                    # E12: the last scheduled run (step names, durations, counts)
        from coach.quality import logs as run_logs
        rep["last_run"] = run_logs.last_run(cfg.log_dir)
    except Exception:                                                       # noqa: BLE001
        rep["last_run"] = None
    return rep


@router.get("/analytics/coverage", tags=["analytics"], summary="Which months each account covers")
def coverage(snap: Snapshot = Depends(get_member_snapshot)):
    ds = snap.ds
    return {"as_of": ds.today.isoformat(), "accounts": [r.to_dict() for r in ds.coverage.table()],
            "foreign_transactions": len(ds.foreign), "memory_warnings": ds.memory.warnings}


@router.get("/accounts/balances", tags=["accounts"], summary="Balance per account and the household total")
def balances(sp: ScopeParams = Depends(), snap: Snapshot = Depends(get_member_snapshot)):
    return views.balances(snap.ds, sp.scope(snap.ds))
