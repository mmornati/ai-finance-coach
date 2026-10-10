"""The FastAPI application factory (E5-1)."""
from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.routing import compile_path

from coach import __version__
from coach.api import errors, jobs as jobs_mod, security as sec
from coach.api.coachjobs import CoachJobs
from coach.api.routes import (alerts, analytics, coach, connections, core, household, loans, me, memory, onboarding, optimizer, plans, quality,
                              rental, review, setup, subs, transactions, wealth)
from coach.api.state import CURRENT_ACTOR, AppState
from coach.household import users as users_mod
from coach.config import Config

log = logging.getLogger("coach.api")
DEV_PORTS = (5173, 4173)
STATIC_DIR = Path(__file__).parent / "static"
ASSET_EXT = {"js", "css", "png", "svg", "ico", "webmanifest", "json", "map", "txt", "woff", "woff2", "jpg", "jpeg", "webp", "xml"}
EXCHANGE_PATH = "/api/v1/session/exchange"
API_PREFIX = "/api/v1"
# E14-8: what a CHILD login may call. Everything else is denied (403), whatever its method and whether or not the endpoint exists:
# deny by default, so a new endpoint is closed to a child until someone adds it here on purpose. The data of /me/* is the child's own,
# taken from the login (never from a parameter of the request).
CHILD_ALLOWED = frozenset({
    ("GET", f"{API_PREFIX}/session"), ("POST", f"{API_PREFIX}/session/logout"), ("GET", f"{API_PREFIX}/meta/taxonomy"),
    ("GET", f"{API_PREFIX}/me"), ("GET", f"{API_PREFIX}/me/summary"), ("GET", f"{API_PREFIX}/me/transactions"),
    ("GET", f"{API_PREFIX}/me/preferences"), ("PUT", f"{API_PREFIX}/me/preferences")})


def child_allowed(method: str, path: str) -> bool:
    return (method.upper(), path.rstrip("/") or "/") in CHILD_ALLOWED


ROUTERS = (core, analytics, alerts, plans, subs, optimizer, wealth, loans, rental, transactions, review, memory, onboarding, connections, coach,
           quality, setup, household, me)


def dry_run_table(routers=ROUTERS) -> list[tuple[frozenset, re.Pattern]]:
    """(methods, path regex) of every endpoint that takes a `dry_run` query parameter, i.e. that really previews. FastAPI ignores an
    unknown query parameter, so the middleware must not trust `?dry_run=true` on its own (a plain write would skip the audit row and
    get the lax preview budget)."""
    out = []
    for r in routers:
        for route in r.router.routes:
            dep = getattr(route, "dependant", None)
            if dep is not None and any(p.name == "dry_run" for p in dep.query_params):
                rx, _fmt, _conv = compile_path(API_PREFIX + route.path)
                out.append((frozenset(route.methods or ()), rx))
    return out


def declares_dry_run(table, method: str, path: str) -> bool:
    return any(method in methods and rx.match(path) for methods, rx in table)


DRY_RUN_TABLE = dry_run_table()


def set_session_cookie(app: FastAPI, resp: Response, value: str) -> None:
    s: sec.Security = app.state.security
    resp.set_cookie(s.cookie_name, value, max_age=s.max_age, httponly=True, samesite="strict", path="/",
                    secure=s.secure_cookie)


def create_app(cfg: Config, *, insecure: bool = False, port: Optional[int] = None, dev: bool = False, con=None,
               inline_jobs: bool = False, static_dir: Optional[Path] = None) -> FastAPI:
    port = port or cfg.ui_port
    app = FastAPI(title="AI finance coach", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url="/api/openapi.json",
                  description="Local API of the personal finance coach web app. Money is a decimal string with two "
                              "decimals. Every call needs the session cookie (obtained once from a one-time login link "
                              "printed by `coach ui`) and, for writes, the X-CSRF-Token header. "
                              "AGENTS MUST NOT CALL THIS API: it is for the human at the keyboard.")
    state = AppState(cfg, insecure=insecure, con=con)
    state.jobs = jobs_mod.Jobs(state, inline=inline_jobs)
    state.coach_jobs = CoachJobs(state)
    app.state.coach = state
    app.state.dev = dev
    extra = cfg.ui_allowed_hosts if cfg.ui_allow_remote else ()
    app.state.security = security = sec.Security(
        sec.load_secret(cfg.data_dir, cfg.ui_key_rotation_days), port, extra, DEV_PORTS if dev else (),
        session_hours=cfg.ui_session_hours, secure_cookie=cfg.ui_allow_remote, data_dir=cfg.data_dir)
    static = static_dir or STATIC_DIR
    app.state.static = static
    errors.install(app)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        method = request.method
        is_api = path.startswith("/api")
        if not security.host_allowed(request.headers.get("host")):
            return errors.respond(421, "bad_host", "this host name is not allowed (DNS rebinding protection)")
        cookie = request.cookies.get(security.cookie_name)
        sess = security.parse_session(cookie)
        sid = sess[0] if sess else None
        user = None
        if sess:
            if sess[1] is None:
                user = users_mod.LEGACY                              # the owner login: what `coach ui` always opened
            else:                                                    # a login of E14-8: its role is read NOW, every request
                with state.read() as ucon:
                    user = users_mod.get(ucon, sess[1])
                if user is None or not user.active:
                    sid = None                                       # deleted or disabled: the cookie is worthless
                    user = None
        token_actor = CURRENT_ACTOR.set(user.id if user and user.id != users_mod.LEGACY_ID else None)
        try:
            return await _guarded(request, call_next, path, method, is_api, cookie, sid, user)
        finally:
            CURRENT_ACTOR.reset(token_actor)

    async def _guarded(request, call_next, path, method, is_api, cookie, sid, user):
        preview = False                                              # a real dry run of the matched endpoint (see below)
        if is_api:
            site = request.headers.get("sec-fetch-site")
            if site not in (None, "same-origin", "none"):
                return errors.respond(403, "forbidden", "cross-site request refused")
            ctype = request.headers.get("content-type", "")
            has_body = request.headers.get("content-length", "0") not in ("0", "") or "transfer-encoding" in request.headers
            bad_type = (ctype and not ctype.lower().startswith("application/json")) or (has_body and not ctype)
            if method == "POST" and path == EXCHANGE_PATH:           # the one call that needs no session: it creates it
                if not security.origin_allowed(request.headers.get("origin")):
                    return errors.respond(403, "forbidden", "origin not allowed")
                if bad_type:
                    return errors.respond(415, "unsupported_media_type", "send application/json")
            else:
                if sid is None:
                    return errors.respond(401, "unauthorized", "no valid session: open the one-time login link printed by "
                                                               "`coach ui` (or run `coach ui --login-link`)")
                if user is not None and user.is_child and not child_allowed(method, path):
                    return errors.respond(403, "forbidden_scope", "this login can only see its own data")
                if method in sec.MUTATING:
                    if not security.origin_allowed(request.headers.get("origin")):
                        return errors.respond(403, "forbidden", "origin not allowed")
                    if not security.csrf_valid(cookie, request.headers.get(sec.CSRF_HEADER)):
                        return errors.respond(403, "csrf", "missing or wrong CSRF token (reload the page)")
                    if bad_type:
                        return errors.respond(415, "unsupported_media_type", "send application/json")
                    # a preview only when the MATCHED endpoint really has a dry run: a stray `?dry_run=true` on a plain write must
                    # neither open the lax bucket nor skip the audit row below
                    preview = request.query_params.get("dry_run") == "true" and declares_dry_run(DRY_RUN_TABLE, method, path)
                    bucket = security.preview_bucket if preview else security.write_bucket
                    if not bucket.take(sid):
                        r = errors.respond(429, "rate_limited", "too many changes in a short time: wait a moment")
                        r.headers["Retry-After"] = "5"
                        return r
        request.state.cookie = cookie if sid else None
        request.state.sid = sid
        request.state.user = user
        resp = await call_next(request)
        sec.apply_headers(resp, no_store=is_api, csp=sec.DOCS_CSP if path == "/api/docs" else None)
        if security.secure_cookie:                                   # remote mode (behind TLS): never a plain-http first hop again
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        if is_api and sid is not None and user is not None and method in sec.MUTATING and not preview:
            try:                                                     # E14-8: who changed what (never a payload)
                with state.write(quiet=True) as acon:
                    users_mod.audit(acon, user.id, method, path, resp.status_code)
            except Exception:                                        # noqa: BLE001
                pass
        return resp

    for r in ROUTERS:
        app.include_router(r.router, prefix=API_PREFIX)

    @app.get("/api/docs", include_in_schema=False)
    def docs() -> HTMLResponse:
        return HTMLResponse(_docs_html(app))

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_404(rest: str):
        return errors.respond(404, "not_found", f"no such endpoint /api/{rest}")

    def index_response() -> Response:
        # The page shell carries no data and no cookie: the session starts only from the one-time login link.
        index = static / "index.html"
        if not index.exists():
            return HTMLResponse("<h1>The web app is not built yet</h1><p>Run <code>pnpm install &amp;&amp; pnpm build</code> "
                                "in <code>web/</code>, or use dev mode (see docs/ui.md).</p>", status_code=503)
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(path: str):
        if path:
            f = (static / path).resolve()
            try:
                f.relative_to(static.resolve())
            except ValueError:
                return errors.respond(404, "not_found", "not found")
            if f.is_file() and f.name != "index.html":
                headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") \
                    else {"Cache-Control": "no-cache"}
                if path == "sw.js":
                    headers["Service-Worker-Allowed"] = "/"
                    return FileResponse(f, media_type="text/javascript", headers=headers)
                return FileResponse(f, headers=headers)
            if path.rsplit("/", 1)[-1].rpartition(".")[2].lower() in ASSET_EXT:       # a missing file, not an app route
                return errors.respond(404, "not_found", "not found")
        return index_response()

    return app


def _docs_html(app: FastAPI) -> str:
    spec = app.openapi()
    rows = []
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            params = ", ".join(f"{p['name']}{'*' if p.get('required') else ''}" for p in op.get("parameters", []))
            body = ""
            rb = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema", {})
            if rb:
                body = rb.get("$ref", "").rsplit("/", 1)[-1] or "object"
            resp = ""
            ok = (op.get("responses") or {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {})
            if ok:
                resp = ok.get("$ref", "").rsplit("/", 1)[-1] or ok.get("type", "")
            rows.append(f"<tr><td class='m {method}'>{method.upper()}</td><td><code>{html.escape(path)}</code></td>"
                        f"<td>{html.escape(op.get('summary') or '')}</td><td>{html.escape(params)}</td>"
                        f"<td>{html.escape(body)}</td><td>{html.escape(resp)}</td></tr>")
    schemas = "".join(f"<details><summary>{html.escape(n)}</summary><pre>{html.escape(json.dumps(s, indent=1))}</pre></details>"
                      for n, s in sorted((spec.get("components", {}).get("schemas") or {}).items()))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>API reference</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{{font:14px/1.5 system-ui;margin:2rem auto;max-width:1100px;padding:0 1rem;color:#222}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:.35rem .5rem;text-align:left;vertical-align:top}}
.m{{font-weight:700;font-family:monospace}}.get{{color:#0a6}}.post{{color:#06c}}.put,.patch{{color:#a60}}.delete{{color:#c22}}
pre{{background:#f5f5f5;padding:.6rem;overflow:auto}}code{{font-size:13px}}.warn{{background:#fff4d6;padding:.6rem 1rem;border-radius:6px}}
</style></head><body><h1>{html.escape(spec['info']['title'])} {html.escape(spec['info']['version'])}</h1>
<p class="warn">Local only. Needs the browser session cookie; writes need the <code>X-CSRF-Token</code> header (from
<code>GET /api/v1/session</code>). Agents must not call this API. Raw schema: <a href="/api/openapi.json">openapi.json</a>.
Parameters marked * are required.</p>
<table><thead><tr><th>Method</th><th>Path</th><th>What</th><th>Parameters</th><th>Body</th><th>Response</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table><h2>Models</h2>{schemas}</body></html>"""
