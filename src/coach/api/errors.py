"""One error model for the whole API: ``{"error": {"code", "message", "details"}}``."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from coach.classify.corrections import CorrectionError
from coach.classify.splits import SplitError
from coach.household.attribution import HouseholdError
from coach.ingest.accounts import AccountError
from coach.secrets import SecretBackendError, SecretNotFound
from coach.ingest.auth import ConnectError
from coach.memory.history import HistoryError
from coach.memory.proposals import ProposalError
from coach.memory.store import MemoryStoreError, ValidationFailed
from coach.memory.yamlio import YamlError
from coach.transfers import TransferError

log = logging.getLogger("coach.api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def body(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def respond(status: int, code: str, message: str, details=None) -> JSONResponse:
    """Every error carries the security headers too (CSP, nosniff, no-store...): the 500 handler runs outside the
    middleware, so the headers are added here, not there."""
    from coach.api import security
    r = JSONResponse(body(code, message, details), status_code=status)
    security.apply_headers(r)
    return r


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return respond(e.status, e.code, e.message, e.details)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError):
        details = [{"field": ".".join(str(x) for x in err["loc"] if x != "body"), "message": err["msg"]}
                   for err in e.errors()]
        return respond(422, "validation_error", "the request is not valid: "
                       + "; ".join(f"{d['field']}: {d['message']}" for d in details[:3]), details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
                421: "bad_host"}.get(e.status_code, "http_error")
        return respond(e.status_code, code, str(e.detail))

    @app.exception_handler(ValidationFailed)
    async def _vf(_: Request, e: ValidationFailed):
        return respond(422, "memory_invalid", str(e), [i.to_dict() for i in e.issues])

    @app.exception_handler(ProposalError)
    async def _prop(_: Request, e: ProposalError):
        return respond(409, "proposal_error", str(e))

    @app.exception_handler(MemoryStoreError)
    async def _mem(_: Request, e: MemoryStoreError):
        return respond(409, "memory_error", str(e))

    @app.exception_handler(HistoryError)
    async def _hist(_: Request, e: HistoryError):
        return respond(409, "history_error", str(e))

    @app.exception_handler(YamlError)
    async def _yaml(_: Request, e: YamlError):
        return respond(422, "yaml_error", str(e))

    for exc in (AccountError, TransferError, SplitError, CorrectionError, ConnectError, HouseholdError):
        @app.exception_handler(exc)
        async def _bad(_: Request, e, _exc=exc):
            return respond(400 if not isinstance(e, ConnectError) else 409, "rejected", str(e))

    @app.exception_handler(SecretNotFound)
    async def _secret(_: Request, e: SecretNotFound):
        # names the missing secret only: never a value, and never the path of the secrets folder (that is in str(e), for the terminal)
        name = getattr(e, "name", None)
        return respond(409, "not_configured", f"the secret '{name}' is not configured: `coach config set-secret {name}`" if name
                       else "a secret is not configured: `coach security audit` lists them")

    @app.exception_handler(SecretBackendError)
    async def _secret_backend(_: Request, e):
        return respond(409, "not_configured", "the secret store cannot be read (unlock or allow the Keychain, or check the secrets folder)")

    @app.exception_handler(Exception)
    async def _any(_: Request, e: Exception):
        log.exception("unhandled error")
        # never echo internals, not even the exception type (a database error may quote SQL, a client error a URL)
        return respond(500, "internal_error", "unexpected error; see the terminal running `coach ui`")
