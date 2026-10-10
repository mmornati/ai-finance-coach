"""Passkeys (E16): the login page's "Sign in with a passkey" (no session) and a login's own passkeys (session)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from coach.api import passkeys as pk
from coach.api.deps import get_state
from coach.api.errors import ApiError
from coach.api.state import AppState
from coach.household import users as users_mod

router = APIRouter()


def _enabled(request: Request) -> None:
    if not request.app.state.coach.cfg.ui_passkeys:
        raise ApiError(403, "passkeys_disabled", "passkeys are not enabled: set [ui] passkeys = true")


def _login_of(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return pk.OWNER if user is None or user.id == users_mod.LEGACY_ID else user.id


class VerifyRequest(BaseModel):
    challenge_id: str = Field(min_length=1, max_length=64)
    credential: dict


class RegisterRequest(VerifyRequest):
    label: str = Field("", max_length=pk.LABEL_MAX)


class DeleteRequest(BaseModel):
    id: str = Field(min_length=1, max_length=400)


@router.get("/session/methods", tags=["core"], summary="How a session can start here (no session needed)")
def methods(request: Request, state: AppState = Depends(get_state)):
    sso = request.app.state.sso
    return {"passkeys": state.cfg.ui_passkeys, "sso": sso is not None, "sso_sign_out": sso.sign_out if sso else None}


@router.post("/session/passkey/options", tags=["core"], summary="A challenge for 'Sign in with a passkey' (no session needed)")
def login_options(request: Request):
    _enabled(request)
    sec = request.app.state.security
    return pk.authentication_options(request.app.state.challenges, request, sec)


@router.post("/session/passkey/verify", tags=["core"], summary="Trade a passkey assertion for the session cookie")
def login_verify(req: VerifyRequest, request: Request, state: AppState = Depends(get_state)):
    _enabled(request)
    from coach.api.app import set_session_cookie
    sec = request.app.state.security
    if not sec.passkey_bucket.take("login"):
        raise ApiError(429, "rate_limited", "too many login attempts: wait a few minutes")
    bad = ApiError(401, "passkey_rejected", "this passkey was not accepted here: try again, or open a one-time login link")
    with state.write(quiet=True) as con:
        login = pk.authenticate(con, request.app.state.challenges, request, sec, req.challenge_id, req.credential)
    if not login:
        raise bad
    user: Optional[str] = None
    if login != pk.OWNER:                                        # a login of E14-8: it must still exist and be enabled
        with state.read() as con:
            u = users_mod.get(con, login)
        if u is None or not u.active:
            raise bad
        user = login
    resp = JSONResponse({"ok": True})
    set_session_cookie(request.app, resp, sec.new_cookie(user=user))
    return resp


@router.get("/session/passkeys", tags=["core"], summary="The passkeys of this login")
def my_passkeys(request: Request, state: AppState = Depends(get_state)):
    with state.read() as con:
        return {"enabled": state.cfg.ui_passkeys, "passkeys": pk.listing(con, _login_of(request))}


@router.post("/session/passkeys/options", tags=["core"], summary="A challenge to enrol a passkey for this login")
def register_options(request: Request, state: AppState = Depends(get_state)):
    _enabled(request)
    with state.read() as con:
        return pk.registration_options(con, request.app.state.challenges, request, request.app.state.security, _login_of(request))


@router.post("/session/passkeys", tags=["core"], summary="Enrol the passkey the browser just made for this login")
def register(req: RegisterRequest, request: Request, state: AppState = Depends(get_state)):
    _enabled(request)
    with state.write(quiet=True) as con:
        return pk.register(con, request.app.state.challenges, request, request.app.state.security, _login_of(request), req.challenge_id,
                           req.credential, req.label)


@router.post("/session/passkeys/delete", tags=["core"], summary="Remove one of this login's passkeys")
def delete(req: DeleteRequest, request: Request, state: AppState = Depends(get_state)):
    with state.write(quiet=True) as con:
        if not pk.remove(con, _login_of(request), req.id):
            raise ApiError(404, "not_found", "no such passkey on this login")
    return {"ok": True}
