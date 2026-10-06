"""`coach ui`: serve the web app on the loopback interface, and hand out the one-time login link."""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

from coach.api import security as sec
from coach.config import Config, ConfigError


def container_marker() -> bool:
    from coach import home as home_mod
    return home_mod.on_container_filesystem()


def container_bind_active(cfg: Config, host: str) -> bool:
    return host in ("0.0.0.0", "::") and cfg.ui_container_bind and not cfg.ui_allow_remote and container_marker()


def check_bind(cfg: Config, host: str) -> None:
    """Loopback only, unless [ui] allow_remote = true. Remote use (a non-loopback bind, or hosts accepted from a proxy such
    as `tailscale serve`) additionally needs `remote_tls_ack = true` (you confirm that an HTTPS proxy terminates TLS in front
    of this server) and the names to accept in `allowed_hosts`; the one-time login link is required everywhere.

    Inside the project's container the app must listen on the container's own interface to be reachable through the published port. 0.0.0.0 / ::
    are accepted ONLY when BOTH hold: this process really runs in a container (`home.on_container_filesystem()`: /.dockerenv, /run/.containerenv or a
    container runtime in PID 1's cgroup: not an environment variable) AND the configuration says `[ui] container_bind = true` (written only by the image's
    `coach init`). Anything else is refused as before. The published port must be on the host's 127.0.0.1 only: docker-compose.yml does it and a test keeps it so."""
    if host in ("0.0.0.0", "::") and not cfg.ui_allow_remote and cfg.ui_container_bind:
        if not container_marker():
            raise ConfigError(f"refusing to listen on {host!r}: [ui] container_bind = true, but this process is not running in a container "
                              "(no /.dockerenv, /run/.containerenv or container cgroup). Remove container_bind from your configuration.")
        return
    if not sec.is_loopback(host) and not cfg.ui_allow_remote:
        raise ConfigError(f"refusing to listen on {host!r}: the web app shows your bank data and is local only. "
                          "Set [ui] allow_remote = true to allow it (and put it behind Tailscale or a VPN, never on the "
                          "open internet). (Inside the project's container, [ui] container_bind = true is what the image's `coach init` writes.)")
    if not cfg.ui_allow_remote:
        return
    if not cfg.ui_remote_tls_ack:
        raise ConfigError("[ui] allow_remote needs [ui] remote_tls_ack = true: confirm that an HTTPS proxy terminates TLS in "
                          "front of this server (for example `tailscale serve --bg 8765`); plain http over a network would "
                          "expose your session")
    if not cfg.ui_allowed_hosts:
        raise ConfigError("[ui] allow_remote needs [ui] allowed_hosts = [\"your-machine.tailnet.ts.net\"]: the Host names to accept")


def login_url(cfg: Config, port: int, host: str, dev: bool, token: str) -> str:
    """The one-time link. The token sits in the fragment: a browser never sends it to a server, a log or a Referer."""
    if dev:
        origin = "http://localhost:5173"
    elif cfg.ui_allow_remote and cfg.ui_allowed_hosts:
        origin = f"https://{cfg.ui_allowed_hosts[0]}"
    else:
        origin = f"http://127.0.0.1:{port}"
    return f"{origin}/login#t={token}"


def write_login_link(cfg: Config, url: str):
    """The login link in a private file of the data volume (container mode without a terminal): 0600, replaced each start."""
    from coach.db import ensure_private_dir
    ensure_private_dir(cfg.data_dir)
    dest = cfg.data_dir / "login-link.txt"
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(url + "\n")
    os.chmod(dest, 0o600)
    return dest


def cmd_ui(a, cfg: Config) -> None:
    host = a.host or cfg.ui_host
    port = a.port or cfg.ui_port
    if getattr(a, "rotate_session_key", False):
        sec.rotate_secret(cfg.data_dir)
        print("session key rotated: every browser session is signed out; run `coach ui` for a new login link")
        return
    login = getattr(a, "user", None)
    if login and not getattr(a, "login_link", False):
        sys.exit("error: --user goes with --login-link (the link of one person's login: `coach users list`)")
    if login:                                              # E14-8: the link opens THAT login, which must exist and be enabled
        from coach import db as db_mod
        from coach.household import users as users_mod
        u = users_mod.get(db_mod.connect(cfg, insecure=a.insecure), login)
        if u is None or not u.active:
            sys.exit(f"error: no enabled login {login!r} (`coach users list`, `coach users add`)")
    if getattr(a, "login_link", False):
        token = sec.LoginTokens(cfg.data_dir).issue(user=login)
        print(login_url(cfg, port, host, a.dev, token))
        print(f"one-time link, valid {sec.LOGIN_TTL // 60} minutes: open it in the browser that should get access "
              "(works with a running `coach ui`)", file=sys.stderr)
        return
    import uvicorn
    from coach.api.app import create_app

    check_bind(cfg, host)
    sys.stdout.reconfigure(line_buffering=True)          # the login link must appear at once, even when piped to a log
    app = create_app(cfg, insecure=a.insecure, port=port, dev=a.dev)
    if not a.dev and not (app.state.static / "index.html").exists():
        print(f"warning: the web app is not built ({app.state.static}/index.html is missing): run `pnpm install && pnpm build` "
              "in web/ (or `coach ui --dev` with the Vite dev server). The API still works.", file=sys.stderr)
    if not sec.is_loopback(host):
        print(f"WARNING: listening on {host}: reach it only over HTTPS through Tailscale or a VPN, never the internet.",
              file=sys.stderr)
    url = login_url(cfg, port, host, a.dev, sec.LoginTokens(cfg.data_dir).issue())
    in_container = container_bind_active(cfg, host)
    print(f"coach web app on port {port}  (Ctrl-C to stop)" + ("  [dev mode: API only, run `pnpm dev` in web/]" if a.dev else ""))
    if in_container:
        print(f"WARNING: container mode, listening on {host}. The port MUST be published on the host's 127.0.0.1 ONLY "
              "(docker run -p 127.0.0.1:8765:8765, or the shipped docker-compose.yml). A port published on 0.0.0.0 exposes your bank data to the network.",
              file=sys.stderr)
    if in_container and not sys.stdout.isatty():
        # `docker logs` is readable by anyone who can reach the Docker socket, and the link in it is a login: not printed there.
        dest = write_login_link(cfg, url)
        print(f"\nThe one-time login link was NOT printed (container logs are not private). It is in {dest} (mode 0600, replaced at each start); "
              "get a fresh one with: docker compose exec coach coach ui --login-link\n")
    else:
        print(f"\nOpen this one-time login link in your browser (valid {sec.LOGIN_TTL // 60} minutes, single use):\n\n  {url}\n")
        print("Need another one later? Run `uv run coach ui --login-link` in a terminal.")
    if cfg.ui_open_browser and not a.no_browser and sec.is_loopback(host) and not a.dev:
        threading.Thread(target=lambda: (time.sleep(0.8), webbrowser.open(url)), daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
