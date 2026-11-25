"""``POST /mcp``: an MCP streamable HTTP endpoint on the web app's own server that exposes ONE tool, ``brief`` (for Horae).

Why on the web server and not a separate service: the brief reads the same encrypted database, memory and analytics snapshot the web
app already holds (one process, one key, the warm dataset), and the deployment behind a reverse proxy stays one container and one
port. The endpoint is a plain ASGI route mounted before the page routes; it shares nothing with the browser session model.

Access (all enforced here, whatever the proxy does):

* **Off unless a token is configured**: the secret ``horae_brief_token`` (environment ``COACH_HORAE_BRIEF_TOKEN``, the file
  ``/run/secrets/horae_brief_token`` with the file backend, or the Keychain), at least :data:`MIN_TOKEN` characters. Without it, or with
  ``[privacy] local_only`` / ``offline`` (the brief leaves this machine), ``/mcp`` answers 404 like any unknown path.
* **Bearer token**: ``Authorization: Bearer <token>``, compared in constant time (``hmac.compare_digest``). Missing or wrong: 401, and the
  body is not read. The token is read once at start (restart to rotate it) and is never logged; neither is a request or a response body.
* **Bounded**: a request body over :data:`MAX_REQUEST_BYTES` is refused (413), the answer is one document of at most
  :data:`coach.horae.brief.MAX_BYTES`, no compression, ``Cache-Control: no-store``.
* **Only ``brief``**: the finance tools of ``coach mcp serve`` are not reachable here. Stateless JSON mode: no session is kept between
  requests, nothing is streamed: only POST is served (GET and DELETE answer 405 before the MCP layer sees them, since a GET would open an
  event stream that never ends), and a request that is not complete within :data:`REQUEST_TIMEOUT` seconds is cut.
* The web app's guard middleware still applies first (the ``Host`` check against ``[ui] allowed_hosts``). Each answer is journaled through
  ``egress.allow("horae.brief")`` (size only, never content).
"""
from __future__ import annotations

import datetime as dt
import hmac
import json
import logging
from typing import Callable, Optional

import anyio

log = logging.getLogger("coach.horae")

PATH = "/mcp"
TOOL = "brief"
TOKEN_SECRET = "horae_brief_token"
MIN_TOKEN = 32
MAX_REQUEST_BYTES = 64 * 1024
REQUEST_TIMEOUT = 30.0          # seconds for a whole request (waiting for the lock, reading the body, computing, answering)
SERVER_NAME = "finance-coach-brief"
INPUT_SCHEMA = {"type": "object", "additionalProperties": False,
                "properties": {"schema_version": {"type": "integer", "minimum": 1, "default": 1,
                                                  "description": "the highest major version of the brief contract the caller understands"}}}
DESCRIPTION = ("Today's household finance brief as a Horae brief v1 document: a headline, alerts, tasks and links. Aggregates only "
               "(counts, a month-to-date total, budget percentages), never a transaction, a merchant, an account or a person. Read-only.")


def load_token(cfg) -> tuple[Optional[str], str]:
    """(token, state): the token when the endpoint is on, and why it is off otherwise (`no_token`, `too_short`, `unreadable`,
    `privacy`). Never raises, never logs the value."""
    if getattr(cfg, "privacy_offline", False) or getattr(cfg, "privacy_local_only", False):
        return None, "privacy"
    from coach import secrets
    try:
        token = secrets.get_secret(TOKEN_SECRET, required=False)
    except Exception as e:                                                 # noqa: BLE001 - a locked Keychain, a bad secrets folder
        log.warning("horae brief endpoint off: the secret %s could not be read (%s)", TOKEN_SECRET, type(e).__name__)
        return None, "unreadable"
    token = (token or "").strip()
    if not token:
        return None, "no_token"
    if len(token) < MIN_TOKEN or not token.isascii() or not token.isprintable():
        log.warning("horae brief endpoint off: the secret %s must be at least %d printable ASCII characters", TOKEN_SECRET, MIN_TOKEN)
        return None, "too_short"
    return token, "enabled"


def bearer(headers: list) -> Optional[bytes]:
    """The token of an ``Authorization: Bearer ...`` header (one header, the scheme in any case), or None."""
    values = [v for k, v in headers if k.lower() == b"authorization"]
    if len(values) != 1:
        return None
    scheme, _, value = values[0].partition(b" ")
    if scheme.lower() != b"bearer" or not value.strip():
        return None
    return value.strip()


def build_server(answer: Callable[[], dict]):
    """The MCP server: `tools/list` shows `brief` only, `tools/call` runs `answer` in a worker thread (it reads the database)."""
    import mcp_types as types
    from mcp.server import Server

    tool = types.Tool(name=TOOL, description=DESCRIPTION, input_schema=INPUT_SCHEMA,
                      annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                                                        open_world_hint=False))

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[tool])

    def refuse(text: str):
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)

    async def call_tool(ctx, params):
        if params.name != TOOL:
            return refuse(f"unknown tool: only {TOOL!r} is served here")
        args = dict(params.arguments or {})
        version = args.pop("schema_version", 1)
        if args or isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return refuse("invalid arguments: brief takes only schema_version, an integer >= 1")
        try:
            doc = await anyio.to_thread.run_sync(answer)
        except Exception as e:                                             # noqa: BLE001 - never an exception text to the caller
            log.warning("horae brief: the answer failed (%s)", type(e).__name__)
            return refuse("the brief failed on this server")
        if doc is None:
            return refuse("the brief is refused by this server's privacy settings")
        text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], structured_content=doc, is_error=False)

    return Server(SERVER_NAME, version="1", instructions=DESCRIPTION, on_list_tools=list_tools, on_call_tool=call_tool)


async def _plain(send, status: int, code: str, message: str, headers: tuple = ()) -> None:
    from coach.api import security
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    hdrs += [(k.lower().encode(), v.encode()) for k, v in security.SECURITY_HEADERS.items()]
    hdrs += [(b"cache-control", b"no-store")] + [(k, v) for k, v in headers]
    await send({"type": "http.response.start", "status": status, "headers": hdrs})
    await send({"type": "http.response.body", "body": body})


class BriefEndpoint:
    """The ASGI app of ``/mcp``. `answer()` returns the brief (or None when the privacy settings refuse it at call time)."""

    def __init__(self, token: Optional[str], state: str, answer: Callable[[], Optional[dict]]):
        self._token = token.encode() if token else None
        self.state = state
        self.answer = answer
        self._lock = anyio.Lock()

    @property
    def enabled(self) -> bool:
        return self._token is not None

    def authorized(self, headers: list) -> bool:
        given = bearer(headers)
        # compare_digest takes as long for a wrong token of any content; only the length of the configured token can leak, and it is public
        return self._token is not None and given is not None and hmac.compare_digest(given, self._token)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        if not self.enabled:
            await _plain(send, 404, "not_found", "not found")
            return
        headers = scope.get("headers") or []
        if not self.authorized(headers):
            await _plain(send, 401, "unauthorized", "a valid bearer token is required",
                         ((b"www-authenticate", b'Bearer realm="coach-brief"'),))
            return
        if scope.get("method") != "POST":
            await _plain(send, 405, "method_not_allowed", "POST only", ((b"allow", b"POST"),))
            return
        length = next((v for k, v in headers if k.lower() == b"content-length"), b"0")
        if not length.isdigit() or int(length) > MAX_REQUEST_BYTES:
            await _plain(send, 413, "too_large", "request too large")
            return
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        started = False

        async def send_no_store(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            if message["type"] == "http.response.start":
                hs = [(k, v) for k, v in message.get("headers", []) if k.lower() not in (b"cache-control", b"content-encoding")]
                message = {**message, "headers": hs + [(b"cache-control", b"no-store")]}
            await send(message)

        # one stateless manager per request: nothing outlives the request, and the app needs no lifespan hook
        manager = StreamableHTTPSessionManager(build_server(self._answer_once), json_response=True, stateless=True,
                                               max_request_body_size=MAX_REQUEST_BYTES)
        # one request at a time, and never for longer than REQUEST_TIMEOUT: a stalled body or a slow client cannot hold the endpoint
        with anyio.move_on_after(REQUEST_TIMEOUT) as cut:
            async with self._lock, manager.run():
                await manager.handle_request(scope, receive, send_no_store)
        if cut.cancelled_caught and not started:
            await _plain(send, 408, "timeout", "request timed out")

    def _answer_once(self) -> Optional[dict]:
        return self.answer()


def web_answer(app_state, now: Callable[[], dt.datetime]) -> Callable[[], Optional[dict]]:
    """The brief of the web app's data: its analytics snapshot, its read-only second connection, its memory store and review queue."""
    from coach import egress
    from coach.horae import brief as brief_mod

    def answer() -> Optional[dict]:
        cfg = app_state.cfg
        t = now()

        def load():
            from coach.api.routes.review import queue
            snap = app_state.snapshot()
            rows = queue(app_state, 0.7)
            with app_state.heavy() as con:
                facts = brief_mod.collect(con, cfg, snap.ds, app_state.store, review_rows=rows, now=t, recurring=snap.recurring())
                violations = brief_mod.violations_for(app_state.store, con, cfg)
            return facts, violations

        doc = brief_mod.produce(load, now=t, base=brief_mod.link_base(cfg))
        size = len(json.dumps(doc, separators=(",", ":")).encode())
        try:
            egress.allow("horae.brief", {"bytes": size, "purpose": "brief", "host": "(Horae)"}, cfg=cfg)
        except egress.EgressDenied:
            return None
        return doc
    return answer


def mount(app, app_state, cfg, now: Optional[Callable[[], dt.datetime]] = None) -> BriefEndpoint:
    """Add ``/mcp`` to the FastAPI app (call it before the catch-all page route) and return the endpoint (``app.state.brief``)."""
    from starlette.routing import Route
    token, state = load_token(cfg)
    endpoint = BriefEndpoint(token, state, web_answer(app_state, now or (lambda: dt.datetime.now(dt.timezone.utc))))
    app.router.routes.append(Route(PATH, endpoint=endpoint, methods=["GET", "POST", "DELETE"], include_in_schema=False))
    if endpoint.enabled:
        log.info("horae brief endpoint on at %s", PATH)
    return endpoint
