"""Where a coach installation lives, and the files shipped with the package (E13-1).

* **The home** is the folder holding ``config.toml`` (every relative path of the configuration is resolved against it). In a source
  checkout it is the checkout; for an installed package (``uvx``, ``pipx``) it is ``$COACH_HOME`` or ``~/.ai-finance-coach``.
* **The templates** are what ``coach init`` copies into a fresh home: the commented configuration, the memory skeleton (no household data),
  the CSV import profile examples. In the wheel they sit under ``coach/templates``; in a checkout the configuration example and the
  profiles are the ones at the repository root (one source of truth, no copy to keep in sync).
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
CHECKOUT_ROOT = PACKAGE_DIR.parents[1]
DEFAULT_HOME_NAME = ".ai-finance-coach"


def in_checkout() -> bool:
    """True when this package runs from a source checkout (the project root holds pyproject.toml and src/coach)."""
    return (CHECKOUT_ROOT / "pyproject.toml").is_file() and (CHECKOUT_ROOT / "src" / "coach").is_dir()


def default_home() -> Path:
    """The home of an installed package: $COACH_HOME, else ~/.ai-finance-coach."""
    if env := os.getenv("COACH_HOME"):
        return Path(env).expanduser().resolve()
    return (Path.home() / DEFAULT_HOME_NAME).resolve()


def config_template() -> Path:
    """The commented configuration `coach init` copies."""
    packaged = TEMPLATES_DIR / "config.example.toml"
    return packaged if packaged.is_file() else CHECKOUT_ROOT / "config.example.toml"


def import_profiles_template() -> Path:
    packaged = TEMPLATES_DIR / "import_profiles"
    return packaged if packaged.is_dir() else CHECKOUT_ROOT / "config" / "import_profiles"


def memory_template() -> Path:
    return TEMPLATES_DIR / "memory"


def template_files(root: Path) -> list[Path]:
    """Every file below `root`, relative to it, sorted (an empty list when `root` does not exist)."""
    if not root.is_dir():
        return []
    return sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def in_container() -> bool:
    """A real container (marker file / cgroup) or the image's own environment flag: for HINTS only (never for a security decision)."""
    return on_container_filesystem() or bool(os.environ.get("COACH_IN_CONTAINER"))


def on_container_filesystem(paths=("/.dockerenv", "/run/.containerenv"), cgroup: str = "/proc/1/cgroup") -> bool:
    """True when this process really runs inside a container: Docker's or Podman's marker file, or a container runtime in PID 1's cgroup.
    Independent of the environment (an environment variable can be set by anyone; these files cannot be created by an unprivileged user)."""
    if any(os.path.exists(p) for p in paths):
        return True
    try:
        text = Path(cgroup).read_text()
    except OSError:
        return False
    return any(k in text for k in ("docker", "kubepods", "containerd", "libpod"))
