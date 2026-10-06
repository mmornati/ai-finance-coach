"""Bank authorisation flows: connect / reconnect / finish, one consent owner per bank."""
from __future__ import annotations

import json
import re
import uuid
import webbrowser
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from coach.db import now_iso
from coach.ingest import consent as consent_mod
from coach.ingest.accounts import store_session
from coach.ingest.callback import CallbackError, CallbackServer, Outcome, container_bind_allowed


class ConnectError(Exception):
    pass


CLASH_HELP = (
    "A bank allows few concurrent authorisations per application and user: a new consent created from the same "
    "Enable Banking app can invalidate the previous one at some banks. If another tool (for example the BankMCP "
    "server) uses the same Enable Banking app, it must NOT hold a second consent for this bank: only one owner "
    "per bank, otherwise one of them silently stops working at the next sync.")


def active_session_for(con, bank: str, country: str, now=None):
    """The non-dead, non-replaced session already covering this ASPSP, if any (a Consent)."""
    for c in consent_mod.list_consents(con, now):
        if c.bank.lower() == bank.lower() and c.country.upper() == country.upper() and c.status not in consent_mod.DEAD:
            return c
    return None


def check_single_consent(con, bank: str, country: str, replace: bool, now=None) -> None:
    c = active_session_for(con, bank, country, now)
    if c and not replace:
        raise ConnectError(
            f"{c.bank} ({c.country}) already has an active consent (session {c.session_id}, valid until "
            f"{c.valid_until}). Refusing to start a second one.\n{CLASH_HELP}\n"
            f"To renew it use `coach reconnect \"{c.bank}\"`, or pass --replace to connect again "
            f"(the old session is retired once the new one is completed).")


def find_session(con, target: str):
    """Session (Consent) for a reconnect target: session id, or bank name (case-insensitive substring)."""
    cons = consent_mod.list_consents(con)
    exact = [c for c in cons if c.session_id == target]
    if exact:
        return exact[0]
    low = target.lower()
    same = [c for c in cons if c.bank.lower() == low] or [c for c in cons if low in c.bank.lower()]
    if len(same) == 1:
        return same[0]
    if not same:
        raise ConnectError(f"No bank session matches {target!r} (see `coach consents`)")
    raise ConnectError(f"{target!r} matches several sessions: "
                       + ", ".join(f"{c.bank} ({c.session_id})" for c in same) + "; pass the session id")


PENDING_MAX_AGE = timedelta(hours=24)


def is_expired(created_at: str | None, now: datetime | None = None) -> bool:
    """A pending authorisation older than 24 h is dead (the bank's login link is short-lived). An unreadable
    timestamp is treated as 'not expired' (legacy rows)."""
    d = consent_mod.parse_ts(created_at)
    return d is not None and (now or datetime.now(timezone.utc)) - d > PENDING_MAX_AGE


def start_auth(con, client, cfg, bank: str, country: str, days: int, replaces: str | None = None):
    redirect = cfg.require_redirect()
    state = str(uuid.uuid4())
    valid_until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    res = client.call("POST", "/auth", json={
        "access": {"valid_until": valid_until},
        "aspsp": {"name": bank, "country": country},
        "state": state,
        "redirect_url": redirect,
        "psu_type": "personal",
    })
    con.execute("INSERT INTO pending_auth(state, aspsp_name, aspsp_country, created_at, replaces_session_id, "
                "valid_until_requested) VALUES (?,?,?,?,?,?)", (state, bank, country, now_iso(), replaces, valid_until))
    con.commit()
    return state, res["url"]


def _finalize(con, info: dict) -> dict:
    others = [c for c in consent_mod.list_consents(con)
              if c.bank.lower() == info["bank"].lower() and c.session_id != info["session_id"]
              and c.status not in consent_mod.DEAD]
    info["other_active_sessions"] = [c.session_id for c in others]
    return info


def _store_saved(con, state: str, response: dict, replaces, fallback) -> dict:
    sid = response.get("session_id", "?") if isinstance(response, dict) else "?"
    try:
        info = store_session(con, response, replaces, fallback)
    except Exception as e:
        if con.in_transaction:
            con.rollback()
        raise ConnectError(
            f"the bank created session {sid} but storing it failed ({type(e).__name__}: {e}). The authorisation "
            f"code is single-use, but the response is saved: fix the cause and run "
            f"`coach finish --replay {state}`") from e
    con.execute("UPDATE pending_auth SET session_response=NULL WHERE state=?", (state,))
    con.commit()
    return _finalize(con, info)


def _friendly(e: Exception) -> str:
    """An error text for the terminal that never carries the bank's response body (it could echo the code)."""
    status = getattr(e, "status", None)
    if status in (400, 401, 403, 404, 422):
        return ("the bank rejected the authorisation code: it is single-use and expires within minutes (or was already used). "
                f"Run connect again: {cmd_prefix()} connect --bank <bank> --country <XX>")
    return str(e)


def complete_auth(con, client, code: str, state: str | None = None) -> dict:
    """Exchange the authorisation code for a session and store it. Shared by `finish` and the local callback.
    With a `state`, it must be a pending (not completed, not older than 24 h) one, claimed atomically; the session
    it replaces is retired. The raw /sessions response is saved BEFORE it is interpreted, so a storage failure
    never loses a (single-use) code: see :func:`replay_auth`."""
    replaces, fallback = None, {}
    if state:
        row = con.execute("SELECT completed_at, replaces_session_id, created_at, aspsp_name, aspsp_country, "
                          "valid_until_requested FROM pending_auth WHERE state=?", (state,)).fetchone()
        if not row:
            raise ConnectError("Unknown state: not started by this tool")
        if row[0]:
            raise ConnectError(f"This authorisation was already completed on {row[0]}")
        if is_expired(row[2]):
            raise ConnectError(f"This authorisation was started on {row[2]}, more than 24 h ago: it expired. "
                               "Start again with `coach connect` / `coach reconnect`.")
        replaces = row[1]
        fallback = {"bank": row[3], "country": row[4], "valid_until": row[5]}
        # claim atomically BEFORE calling the bank: of two concurrent completers (callback + `finish`) only
        # the one whose UPDATE changes the row proceeds
        claimed = con.execute("UPDATE pending_auth SET completed_at=? WHERE state=? AND completed_at IS NULL",
                              (now_iso(), state))
        con.commit()
        if claimed.rowcount != 1:
            raise ConnectError("This authorisation is already being completed (or was completed) elsewhere")
    else:  # bare code: record an ad-hoc row so the response can still be replayed
        state = "adhoc-" + str(uuid.uuid4())
        con.execute("INSERT INTO pending_auth(state, created_at, completed_at) VALUES (?,?,?)",
                    (state, now_iso(), now_iso()))
        con.commit()
    try:
        s = client.call("POST", "/sessions", json={"code": code})
    except Exception:
        # nothing came back: release the claim so `coach finish` can retry
        if con.in_transaction:
            con.rollback()
        con.execute("UPDATE pending_auth SET completed_at=NULL WHERE state=?", (state,)) if not state.startswith("adhoc-") \
            else con.execute("DELETE FROM pending_auth WHERE state=?", (state,))
        con.commit()
        raise
    con.execute("UPDATE pending_auth SET session_response=? WHERE state=?", (json.dumps(s), state))
    con.commit()
    return _store_saved(con, state, s, replaces, fallback)


def replay_auth(con, state: str) -> dict:
    """Store the /sessions response saved by a failed completion (no bank call)."""
    row = con.execute("SELECT session_response, replaces_session_id, aspsp_name, aspsp_country, "
                      "valid_until_requested FROM pending_auth WHERE state=?", (state,)).fetchone()
    if not row:
        raise ConnectError("Unknown state")
    if not row[0]:
        raise ConnectError("Nothing to replay for this state (no saved session response)")
    return _store_saved(con, state, json.loads(row[0]), row[1],
                        {"bank": row[2], "country": row[3], "valid_until": row[4]})


def clean_pasted(text: str) -> str:
    """Undo what a shell or a terminal does to a pasted address: surrounding quotes and whitespace, a line wrap, backslashes before ? = & (zsh
    escapes them when the address is not quoted)."""
    t = (text or "").strip().strip("'\"\u2018\u2019\u201c\u201d`").strip()
    if t.lower().startswith("http"):
        t = re.sub(r"\s+", "", t)
        t = re.sub(r"\\(?=[?=&#%/:])", "", t)
    return t


def cmd_prefix() -> str:
    """How to start a coach command from where this process runs: in the Docker image, `docker compose run --rm coach`; else `uv run coach`."""
    from coach import home as home_mod
    return "docker compose run --rm coach" if home_mod.in_container() else "uv run coach"


def finish_hint() -> str:
    return f"{cmd_prefix()} finish '<that url>'"


def parse_finish_arg(con, code_or_url: str) -> tuple[str, str | None]:
    """(code, state) from the full redirect URL or a bare code (a shell-escaped or quoted paste is tolerated; the code is never echoed)."""
    code_or_url = clean_pasted(code_or_url)
    if not code_or_url.startswith("http"):
        if "?" in code_or_url or "=" in code_or_url or "/" in code_or_url:
            raise ConnectError("that is not a bare code and not an address starting with https://: paste the whole address the browser shows "
                               "after the bank login, in single quotes")
        return code_or_url, None
    q = parse_qs(urlparse(code_or_url).query)
    state = q.get("state", [None])[0]
    if state and not con.execute("SELECT 1 FROM pending_auth WHERE state=?", (state,)).fetchone():
        raise ConnectError("Unknown state in redirect URL: not started by this tool")
    if "error" in q:
        raise ConnectError(f"The bank returned an error ({q['error'][0][:60]}): nothing was connected. Run connect again.")
    code = q.get("code", [None])[0]
    if not code:
        raise ConnectError("the address has no ?code=... part: paste the WHOLE address the browser shows after the bank login (it can show an error page), "
                           f"exactly as shown, in single quotes, with no backslashes: {cmd_prefix()} finish '<address>'")
    return code, state


def print_completion(info: dict, out=print) -> None:
    out(f"Session {info['session_id']} with {info['bank']} valid until {info['valid_until']}")
    for a in info["accounts"]:
        extra = ""
        if a["remapped"]:
            extra = f"  (same account as existing {a['uid']}: new bank uid {a['api_uid']}, history kept)"
        elif a["new"]:
            extra = "  (new)"
        if a.get("needs_review"):
            extra += (f"  NEEDS REVIEW: could not be matched unambiguously (candidates: "
                      f"{', '.join(a.get('candidates') or []) or 'none'}); it is not synced and left out of analytics "
                      "until you run `coach accounts merge OLD NEW` or `coach accounts set UID --resolve`")
        out(f"  account {a['uid']}  {a['name'] or ''}{extra}")
    if info["retired"]:
        out(f"Retired replaced session(s): {', '.join(info['retired'])}")
    if info["orphans"]:
        out(f"warning: account(s) not returned by the new session, left on the retired one (not synced): "
            f"{', '.join(info['orphans'])}")
    if info.get("other_active_sessions"):
        out(f"warning: {info['bank']} has other active session(s) {info['other_active_sessions']}: "
            "keep one consent owner per bank (see `coach consents`).")
    if info.get("skipped_accounts"):
        out(f"warning: {info['skipped_accounts']} account entr(ies) without a uid were ignored (see the saved response)")
    out(f"\nNext: {cmd_prefix()} sync   (new accounts automatically pull the longest history the bank gives; "
        "reconnected ones continue incrementally)")


def connect_flow(con, client, cfg, bank: str, country: str, days: int, replaces: str | None = None,
                 no_server: bool = False, no_browser: bool = False, timeout: float | None = None,
                 open_browser=webbrowser.open, out=print, on_listening=None) -> int:
    """Start an authorisation and, unless `no_server`, wait for the redirect on the local HTTPS server.
    Returns a process exit code (0 completed / manual flow started, 1 failed or timed out)."""
    redirect = cfg.require_redirect()
    server = None
    holder: dict = {}
    if not no_server:
        def handle(params: dict) -> Outcome:
            state = params.get("state")
            row = con.execute("SELECT created_at FROM pending_auth WHERE state=? AND completed_at IS NULL",
                              (state,)).fetchone() if state else None
            if not state or state != holder.get("state") or not row or is_expired(row[0]):
                return Outcome(False, False, "Unknown or already used state: this redirect was not started by "
                               "the running `coach connect`.", 400)
            if "error" in params:
                return Outcome(True, False, "The bank reported an error or the login was cancelled; "
                               "nothing was connected.", 400)
            code = params.get("code")
            if not code:
                return Outcome(True, False, "The redirect carried no authorisation code.", 400)
            try:
                holder["info"] = complete_auth(con, client, code, state)
            except Exception as e:
                holder["error"] = _friendly(e)
                return Outcome(True, False, "Completing the session failed; see the terminal.", 500)
            return Outcome(True, True, f"{holder['info']['bank']} connected.")
        try:
            bind_all = container_bind_allowed(cfg)
            server = CallbackServer(redirect, handle, tls_dir=cfg.tls_dir, bind_all=bind_all,
                                    timeout=timeout if timeout is not None else cfg.callback_timeout)
            server.bind()
            if bind_all:
                out(f"WARNING: container mode, the redirect server listens on 0.0.0.0:{server.port}. The port MUST be published on the host's 127.0.0.1 ONLY "
                    f"(docker compose run --rm -p 127.0.0.1:{server.port}:{server.port} coach connect ...). Published on 0.0.0.0 it exposes your bank login to the network.")
        except CallbackError as e:
            if "is not local" in str(e) or "must be http" in str(e):
                out(f"note: {e}; falling back to the manual flow (copy the redirect URL).")
                server = None
            else:
                raise ConnectError(str(e)) from e
    try:
        state, url = start_auth(con, client, cfg, bank, country, days, replaces)
        holder["state"] = state
        out("Open this URL and authenticate with your bank (SCA):\n")
        out(url)
        if server is None:
            out("\nWhen your browser lands on the redirect URL (it may show an error page, that's fine),")
            out(f"copy the full address EXACTLY as the browser shows it and run (single quotes, no backslashes):  {finish_hint()}")
            if not no_browser:
                open_browser(url)
            return 0
        out(f"\nWaiting for the bank redirect on {redirect} (timeout {server.timeout:g}s). Your browser will warn "
            "once about the self-signed certificate for localhost: accept it to continue.")
        out(f"If it does not work, run `{finish_hint()}` with the address the browser ends on (exactly as shown, in single quotes).")
        if on_listening:
            on_listening(server.port, state)
        if not no_browser:
            open_browser(url)
        result = server.serve()
    finally:
        if server:
            server.close()
    if result.status == "ok":
        print_completion(holder["info"], out)
        return 0
    if result.status == "timeout":
        out(f"\nTimed out: {result.message}. The authorisation stays pending: finish it with "
            f"`{finish_hint()}` if you completed the login.")
    else:
        out(f"\nFailed: {holder.get('error') or result.message}")
    return 1
