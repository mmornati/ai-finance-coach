"""Print a one-time login link for a DEMO home (used by the screenshot script). Refuses any home without the demo marker.

    uv run python scripts/demo/login_link.py /tmp/coach-demo [--user mia]
"""
import argparse
import os
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("home", type=Path)
ap.add_argument("--user", default=None)
a = ap.parse_args()
home = a.home.expanduser().resolve()
if not (home / ".coach-demo-home").exists():
    sys.exit(f"refusing: {home} is not a demo home")
for line in (home / "demo.env").read_text().splitlines():
    k, _, v = line.removeprefix("export ").partition("=")
    os.environ[k] = v

from coach.api import security as sec          # noqa: E402
from coach.config import load_config           # noqa: E402

cfg = load_config()
token = sec.LoginTokens(cfg.data_dir).issue(user=a.user)
print(f"http://127.0.0.1:{cfg.ui_port}/login#t={token}")
