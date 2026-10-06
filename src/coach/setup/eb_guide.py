"""``coach setup enablebanking``: the guided creation of your OWN Enable Banking application (E13-1).

The coach talks to your banks through Enable Banking (a PSD2 aggregator). For private individuals it is free in "restricted mode": you create
an application in their control panel, link your own accounts to it, and only those accounts are ever returned. Every user needs his own
application and key (the terms forbid sharing one), so nothing is shipped with the coach.

The guide explains each step in the order of the control panel, then (with your agreement, in a terminal) stores the three values in
``config.toml`` (``app_id``, ``redirect_url``, ``private_key_path``) and keeps a private copy of the downloaded key. It validates with
``coach check`` ONLY when you answer yes at that step: that is the one call to api.enablebanking.com. Without a terminal it just prints the guide.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional

from coach import config as config_mod

CONTROL_PANEL = "https://enablebanking.com/cp/applications"
DEFAULT_REDIRECT = "https://localhost:8443/callback"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

GUIDE = """Enable Banking: your own application in restricted mode (free, for your own accounts)

 1. Create an account at https://enablebanking.com and open the control panel ({panel}).
 2. Create a NEW application:
      - environment: Production (the Sandbox only returns fake banks)
      - name: anything you like (for example "my finance coach")
      - redirect URL, exactly:  {redirect}
        (it must be whitelisted in the application; the coach receives the bank's answer there, on your own machine)
 3. When you submit, your browser downloads a PEM private key. "Save this file securely": it signs every request.
    It is NOT stored by Enable Banking, you cannot download it again. Keep it out of any git repository.
 4. Copy the Application ID shown in the control panel.
 5. "Activate by linking accounts": link your OWN bank accounts to the application in the control panel (restricted mode).
    Accounts that are not linked are never returned. Adding a bank or an account later means linking it again there.
 6. Come back here: the coach stores the application id, the redirect URL and a private copy of the key, then (if you say yes)
    checks them with `coach check`, the one call to Enable Banking this guide can make.

Banks allow one active consent per provider and user in some countries (Italy in particular): a new connection can replace an existing one.
"""


def guide_text(redirect: str = DEFAULT_REDIRECT) -> str:
    return GUIDE.format(panel=CONTROL_PANEL, redirect=redirect)


def _is_tty() -> bool:
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())


def _yes(answer: str) -> bool:
    return answer.strip().lower() in ("y", "yes")


def pem_problem(path: Path) -> Optional[str]:
    """None when `path` holds a readable PEM private key; else a short reason (the content is never shown)."""
    try:
        data = path.read_bytes()
    except OSError as e:
        return f"cannot read the file ({type(e).__name__})"
    if b"PRIVATE KEY" not in data[:200]:
        return "it does not look like a PEM private key (no 'PRIVATE KEY' header)"
    try:
        from cryptography.hazmat.primitives import serialization
        serialization.load_pem_private_key(data, password=None)
    except Exception as e:                                                      # noqa: BLE001
        return f"the key cannot be loaded ({type(e).__name__}); it must be an unencrypted PEM"
    return None


def store_key(source: Path, dest_dir: Path) -> Path:
    """A private copy of the downloaded key (folder 0700, file 0600); the original is left where it is."""
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(dest_dir, 0o700)
    except OSError:
        pass
    dest = dest_dir / "eb-private-key.pem"
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(source.read_bytes())
    os.chmod(dest, 0o600)
    return dest


def write_settings(cfg_path: Path, app_id: Optional[str], redirect: Optional[str], key_path: Optional[str]) -> None:
    text = cfg_path.read_text()
    for key, value in (("app_id", app_id), ("redirect_url", redirect), ("private_key_path", key_path)):
        if value:
            text = config_mod.set_toml_value(text, "enable_banking", key, value)
    tmp = cfg_path.with_name(cfg_path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, cfg_path)


def run_guide(cfg, *, isatty: Optional[Callable[[], bool]] = None, input_fn: Callable[[str], str] = input, out=print,
              check_fn: Optional[Callable[[], None]] = None) -> dict:
    """Walk the steps. Returns {"configured": bool, "checked": bool, "changed": [keys]}. `check_fn` runs `coach check` (network)."""
    isatty = isatty or _is_tty
    redirect_now = cfg.eb_redirect_url or DEFAULT_REDIRECT
    out(guide_text(redirect_now))
    res = {"configured": False, "checked": False, "changed": []}
    if not isatty():
        out("(No terminal: this was only the guide. Run `coach setup enablebanking` in a terminal to store the values.)")
        return res
    if not cfg.config_path:
        out("There is no config.toml yet: run `coach init` first.")
        return res
    if input_fn("Did you do steps 1-5 (application created, key downloaded, accounts linked)? [y/N] ").strip().lower() not in ("y", "yes"):
        out("Come back when you have; nothing was changed.")
        return res

    # redirect URL
    redirect = input_fn(f"Redirect URL you whitelisted [{redirect_now}]: ").strip() or redirect_now
    if not redirect.lower().startswith("https://"):
        out("The redirect URL must be https (the bank only redirects to https). Nothing was changed.")
        return res
    # application id
    app_id = input_fn(f"Application ID [{cfg.eb_app_id or ''}]: ").strip() or (cfg.eb_app_id or "")
    if not app_id:
        out("An application id is needed. Nothing was changed.")
        return res
    if not UUID_RE.match(app_id):
        out("Note: an application id normally looks like xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx; check it against the control panel.")
    # key
    key_value = None
    current = cfg.eb_private_key_path
    path_in = input_fn(f"Path of the downloaded .pem key{f' [{current}]' if current else ''}: ").strip().strip("'\"")
    if path_in:
        src = Path(path_in).expanduser()
        problem = pem_problem(src) if src.is_file() else f"{src} is not a file"
        if problem:
            out(f"Problem with the key: {problem}. Nothing was changed.")
            return res
        dest = store_key(src, cfg.root / "enablebanking")
        key_value = str(dest)
        out(f"A private copy was written to {dest} (mode 0600). Delete the downloaded file ({src.name}) once you have it in your password manager or backup.")
    elif not current:
        out("The private key is needed. Nothing was changed.")
        return res
    write_settings(cfg.config_path, app_id, redirect, key_value)
    res["changed"] = [k for k, v in (("app_id", app_id), ("redirect_url", redirect), ("private_key_path", key_value)) if v]
    res["configured"] = True
    out(f"Stored {', '.join(res['changed'])} in {cfg.config_path}.")

    out("\nValidation: `coach check` asks Enable Banking (api.enablebanking.com, GET /application) whether the application id and key are accepted and the "
        "application is active. It sends only your application id, signed with your key.")
    if check_fn is None:
        out("Run `coach check` yourself when you are ready.")
        return res
    if _yes(input_fn("Run it now? [y/N] ")):
        try:
            check_fn()
            res["checked"] = True
        except Exception as e:                                                     # noqa: BLE001
            out(f"The check failed: {type(e).__name__}: {e}")
            out("Fix the value it points at (`coach setup enablebanking` again), or the application is not activated yet (step 5).")
    else:
        out("Skipped. `coach check` does it whenever you want.")
    return res


def cmd_setup_enablebanking(a, cfg, *, isatty=None, input_fn=input, out=print) -> None:
    def check():
        from coach.ingest import commands as ic
        from coach import config as config_mod2
        fresh = config_mod2.load_config(cfg.config_path)          # the values just written
        ic.cmd_check(a, fresh)
    run_guide(cfg, isatty=isatty, input_fn=input_fn, out=out, check_fn=check)
