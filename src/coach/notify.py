"""Optional macOS notification (``osascript``), enabled by ``[notify] macos = true``.

The message is passed as argv to a fixed AppleScript, never interpolated into the script text.
Tests must inject a `runner`; the default subprocess runner is replaced by a failing stub in conftest.
"""
from __future__ import annotations

import subprocess
import sys

SCRIPT = ["on run argv", "display notification (item 1 of argv) with title (item 2 of argv)", "end run"]


def _default_run(cmd, **kw):
    return subprocess.run(cmd, **kw)


def notify_macos(message: str, title: str = "AI finance coach", runner=None) -> bool:
    if runner is None and sys.platform != "darwin":
        return False
    cmd = ["osascript"]
    for line in SCRIPT:
        cmd += ["-e", line]
    cmd += [message, title]
    try:
        r = (runner or _default_run)(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(r, "returncode", 1) == 0
