#!/usr/bin/env bash
# Start the web app on a DEMO home built by scripts/demo/seed_demo.py, with the scripted `claude` of the demo.
#   scripts/demo/run_demo.sh /tmp/coach-demo            # prints a one-time login link
# The home must carry the demo marker: this script never starts on a real home.
set -euo pipefail
HOME_DIR="${1:?usage: run_demo.sh DEMO_HOME}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
[ -f "$HOME_DIR/.coach-demo-home" ] || { echo "refusing: $HOME_DIR is not a demo home (run scripts/demo/seed_demo.py first)" >&2; exit 1; }

# shellcheck disable=SC1091
source "$HOME_DIR/demo.env"
PY="$(cd "$ROOT" && uv run python -c 'import sys; print(sys.executable)')"
mkdir -p "$HOME_DIR/bin"
cat > "$HOME_DIR/bin/claude" <<EOF
#!$PY
import runpy, sys
sys.argv[0] = "$HERE/demo_claude.py"
runpy.run_path("$HERE/demo_claude.py", run_name="__main__")
EOF
chmod +x "$HOME_DIR/bin/claude"
export PATH="$HOME_DIR/bin:$PATH" PYTHONPATH="$ROOT/src" FAKE_CLAUDE=demo
cd "$ROOT"
exec uv run coach ui --no-browser "${@:2}"
