"""Guard against duplicated definitions / undefined names (a duplicated block once slipped into schedule.py)."""
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def test_no_undefined_names_or_redefinitions_in_src():
    p = subprocess.run([sys.executable, "-m", "ruff", "check", str(SRC), "--select", "F821,F811", "--no-cache"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
