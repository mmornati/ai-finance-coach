"""``coach dev release-check``: everything that must be true before the project is published (E13-4). Run it from a source checkout.

Checks (each ``ok`` / ``fail`` / ``skip``; the exit code is 1 when any fails):

* a LICENSE file exists and no ``TODO(license)`` marker is left in a publishable file (the licence is the OWNER's decision: see LICENSE.choose.md)
* the version: pyproject == ``coach.__version__`` == the top section of CHANGELOG.md, which carries a date
* the web app is built (``src/coach/api/static``) and not older than its sources
* hygiene: no personal data in any publishable file or in the sdist list (``coach.hygiene``): the structural rules (keys, home paths, IBANs, e-mails) and the
  real-data terms of the LOCAL term file. A missing term file FAILS the check (``--ci`` skips only that rule and says so)
* the package metadata, the shipped templates and the docs the project promises
* the Dockerfile / docker-compose.yml / .dockerignore static policy (``coach.dockerlint``)
* the CI workflow exists and runs this command
* the test suites: pytest, ruff, vitest and the TypeScript check (``--skip-tests`` / ``--skip-web`` skip them and the run is then NOT release-grade)

Nothing here sends anything anywhere; it runs local tools only.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from coach import __version__, dockerlint, home as home_mod, hygiene

LICENSE_MARKER = "TODO" + "(license)"          # built in two pieces so that this file never contains the marker itself
REPO_MARKER = "TODO" + "(repo-url)"
CONTACT_MARKER = "TODO" + "(contact)"          # the reporting address of SECURITY.md / CODE_OF_CONDUCT.md: the owner's to give
MARKERS = (LICENSE_MARKER, CONTACT_MARKER, REPO_MARKER)
MARKER_EXEMPT = frozenset({"LICENSE.choose.md", "docs/release.md", "src/coach/release.py"})
REQUIRED_DOCS = ("README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md",
                 "docs/architecture.md", "docs/release.md", "docs/docker.md", "docs/claude-settings.example.json")
REQUIRED_TEMPLATES = ("src/coach/templates/memory/README.md", "src/coach/templates/memory/household.yaml",
                      "src/coach/templates/memory/liabilities/_template.yaml", "src/coach/templates/memory/contracts/_template.yaml")
LICENSE_NAMES = ("license", "license.md", "license.txt", "copying", "copying.md")


@dataclass
class Check:
    id: str
    status: str            # ok | fail | skip
    title: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "title": self.title, "detail": self.detail}


def _read(root: Path, rel: str) -> Optional[str]:
    try:
        return (root / rel).read_text()
    except OSError:
        return None


def check_license(root: Path) -> list[Check]:
    found = [p.name for p in root.iterdir() if p.is_file() and p.name.lower() in LICENSE_NAMES and p.stat().st_size > 100]
    if found:
        return [Check("license.file", "ok", "LICENSE file", ", ".join(found))]
    return [Check("license.file", "fail", "LICENSE file",
                  "no LICENSE file: the licence is the owner's decision (MIT or AGPL-3.0, see LICENSE.choose.md); save the chosen text as LICENSE")]


def check_license_markers(root: Path, files: list[Path]) -> Check:
    """No placeholder that needs the OWNER's decision may be left in a publishable file: the licence, the contact address, the repository URL."""
    hits = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        if rel in MARKER_EXEMPT or rel.startswith("tests/") or p.suffix.lower() in hygiene.BINARY_SUFFIXES or p.name in hygiene.LOCK_NAMES:
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        hits += [f"{m} in {rel}" for m in MARKERS if m in text]
    if hits:
        return Check("license.todo", "fail", "Owner placeholders", "still present: " + "; ".join(hits))
    return Check("license.todo", "ok", "Owner placeholders", "no licence / contact / repository placeholder left")


def load_pyproject(root: Path) -> dict:
    try:
        return tomllib.loads((root / "pyproject.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def check_version(root: Path) -> Check:
    proj = load_pyproject(root).get("project", {})
    v = proj.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[a-z0-9.+-]*)?", v or ""):
        return Check("version", "fail", "Version", f"pyproject version {v!r} is not a version number")
    if v != __version__:
        return Check("version", "fail", "Version", f"pyproject says {v} but coach.__version__ is {__version__}: bump both")
    log = _read(root, "CHANGELOG.md") or ""
    m = next((m for m in re.finditer(r"^##\s+\[([^\]]+)\]\s*(?:-\s*(\S+))?", log, re.M) if m.group(1).strip().lower() != "unreleased"), None)
    if not m:
        return Check("version", "fail", "Version", "CHANGELOG.md has no '## [version] - date' section")
    if m.group(1) != v:
        return Check("version", "fail", "Version", f"the top CHANGELOG section is [{m.group(1)}], the version is {v}: add the entry and date it")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", m.group(2) or ""):
        return Check("version", "fail", "Version", f"the CHANGELOG section [{v}] has no release date (write '## [{v}] - YYYY-MM-DD')")
    return Check("version", "ok", "Version", f"{v}, changelog dated {m.group(2)}")


def check_web_built(root: Path) -> Check:
    static = root / "src" / "coach" / "api" / "static"
    index = static / "index.html"
    if not index.is_file():
        return Check("web", "fail", "Web app build", "src/coach/api/static/index.html is missing: run `cd web && pnpm install && pnpm build`")
    refs = re.findall(r'(?:src|href)="/?(assets/[^"]+)"', index.read_text())
    missing = [r for r in refs if not (static / r).is_file()]
    if missing:
        return Check("web", "fail", "Web app build", f"index.html references missing files ({missing[0]}...): rebuild with `pnpm build`")
    web_src = root / "web" / "src"
    if web_src.is_dir():
        newest = max((p.stat().st_mtime for p in web_src.rglob("*") if p.is_file()), default=0)
        if newest > index.stat().st_mtime + 1:
            return Check("web", "fail", "Web app build", "the web sources are newer than the build: run `cd web && pnpm build`")
    return Check("web", "ok", "Web app build", f"built ({len(refs)} asset reference(s) present)")


def sdist_members(root: Path, files: list[Path]) -> list[Path]:
    """The files the sdist would hold: the `include` patterns of pyproject applied to the publishable files, plus the build artifacts (the web app)."""
    sd = load_pyproject(root).get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("sdist", {})
    inc = [i.lstrip("/") for i in sd.get("include", [])]
    exc = [e.lstrip("/").rstrip("/") for e in sd.get("exclude", [])]
    out = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        if rel == "pyproject.toml":
            out.append(p)                                  # always part of an sdist
            continue
        if any(rel == e or rel.startswith(e + "/") for e in exc):
            continue
        if any(rel == i or rel.startswith(i.rstrip("*").rstrip("/") + "/") or (i.endswith("*") and rel.startswith(i[:-1])) for i in inc):
            out.append(p)
    static = root / "src" / "coach" / "api" / "static"
    if static.is_dir() and any("api/static" in a for a in sd.get("artifacts", [])):
        out += [q for q in static.rglob("*") if q.is_file()]
    return out


def scan_dist(root: Path, index) -> list[hygiene.Hit]:
    """Scan the member names and the text members of a built sdist / wheel in dist/ (when there is one)."""
    import tarfile
    import zipfile
    hits: list[hygiene.Hit] = []
    for arc in sorted((root / "dist").glob("*")) if (root / "dist").is_dir() else []:
        pairs = []
        try:
            if arc.suffix == ".whl":
                with zipfile.ZipFile(arc) as z:
                    for n in z.namelist():
                        pairs.append((f"{arc.name}:{n}", z.read(n).decode("utf-8", "ignore") if not n.lower().endswith(tuple(hygiene.BINARY_SUFFIXES)) else ""))
            elif arc.name.endswith(".tar.gz"):
                with tarfile.open(arc) as t:
                    for m in t.getmembers():
                        if m.isfile():
                            data = t.extractfile(m).read().decode("utf-8", "ignore") if not m.name.lower().endswith(tuple(hygiene.BINARY_SUFFIXES)) else ""
                            pairs.append((f"{arc.name}:{m.name}", data))
        except (OSError, tarfile.TarError, zipfile.BadZipFile):
            continue
        hits += hygiene.scan_names(pairs, index)
    return hits


def check_hygiene(root: Path, files: list[Path], *, ci: bool = False, terms: Optional[Path] = None) -> list[Check]:
    """Two checks: the scan itself, and whether the real-data terms were available to it (their absence is a FAILURE, or a skip with --ci)."""
    tpath = terms or hygiene.terms_path(root)
    problem = hygiene.terms_file_problem(tpath, root)
    index = None if problem else hygiene.TermIndex(hygiene.load_terms(tpath))
    members = sdist_members(root, files)
    seen = {p for p in files}
    scan_files = list(files) + [m for m in members if m not in seen]
    hits = hygiene.scan(root, scan_files, index) + scan_dist(root, index)
    out = []
    if hits:
        shown = "; ".join(str(h) for h in hits[:8])
        out.append(Check("hygiene", "fail", "Personal data scan", f"{len(hits)} hit(s) in {len(scan_files)} files (publishable + sdist): {shown}" + (" ..." if len(hits) > 8 else "")))
    else:
        out.append(Check("hygiene", "ok", "Personal data scan", f"{len(scan_files)} files (publishable + sdist), no hit" + (" (structural rules only)" if index is None else f" ({index.count} local terms)")))
    if problem is None:
        out.append(Check("hygiene.local", "ok", "Real-data terms", f"the local term file was used ({index.count} terms)"))
    elif ci:
        out.append(Check("hygiene.local", "skip", "Real-data terms", f"--ci: the real-data rule was NOT run (local term file {problem}); only the structural rules ran. A release needs the local run"))
    else:
        out.append(Check("hygiene.local", "fail", "Real-data terms",
                         f"the LOCAL term file is {problem}: the real-data rule did not run, so the scan proves nothing about your household. "
                         "Run `coach dev hygiene --build-terms` on the machine that holds the data (`--ci` skips this rule in CI and says so)"))
    return out


def check_metadata(root: Path) -> list[Check]:
    data = load_pyproject(root)
    proj = data.get("project", {})
    problems = [k for k in ("name", "version", "description", "readme", "requires-python", "classifiers", "keywords") if not proj.get(k)]
    if "coach" not in (proj.get("scripts") or {}):
        problems.append("scripts.coach")
    out = []
    if problems:
        out.append(Check("metadata", "fail", "Package metadata", "missing in pyproject: " + ", ".join(problems)))
    else:
        out.append(Check("metadata", "ok", "Package metadata", f"{proj['name']} {proj['version']}, python {proj['requires-python']}"))
    wheel = data.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {})
    arts = " ".join(wheel.get("artifacts", []) + list((wheel.get("force-include") or {}).keys()))
    pkg_ok = "api/static" in arts and "config.example.toml" in " ".join((wheel.get("force-include") or {}).keys())
    out.append(Check("package-data", "ok" if pkg_ok else "fail", "Wheel contents",
                     "the built web app, the configuration example and the import profiles are packaged" if pkg_ok else
                     "pyproject must package src/coach/api/static (artifacts) and config.example.toml (force-include)"))
    miss = [t for t in REQUIRED_TEMPLATES if not (root / t).is_file()]
    out.append(Check("templates", "fail" if miss else "ok", "Shipped templates", ("missing: " + ", ".join(miss)) if miss else "memory skeleton present"))
    urls = proj.get("urls") or {}
    out.append(Check("urls", "ok" if urls.get("Homepage") or urls.get("Repository") else "fail", "Project URLs",
                     "set" if urls else f"pyproject [project.urls] is empty ({REPO_MARKER}): add the public repository URL"))
    return out


def check_docs(root: Path) -> Check:
    miss = [d for d in REQUIRED_DOCS if not (root / d).is_file()]
    if not miss:
        try:
            json.loads((root / "docs/claude-settings.example.json").read_text())
        except ValueError:
            return Check("docs", "fail", "Documentation", "docs/claude-settings.example.json is not valid JSON")
    return Check("docs", "fail" if miss else "ok", "Documentation", ("missing: " + ", ".join(miss)) if miss else f"{len(REQUIRED_DOCS)} required documents present")


def check_docker(root: Path) -> Check:
    findings = dockerlint.lint_all(_read(root, "Dockerfile"), _read(root, "docker-compose.yml"), _read(root, ".dockerignore"))
    for doc in ("README.md", "docs/docker.md", "docs/ui.md", "docker-compose.yml"):
        findings += dockerlint.lint_port_examples(_read(root, doc) or "", doc)
    if findings:
        return Check("docker", "fail", "Docker files (static)", "; ".join(str(f) for f in findings[:6]) + (" ..." if len(findings) > 6 else ""))
    return Check("docker", "ok", "Docker files (static)", "Dockerfile, docker-compose.yml and .dockerignore pass the policy (not built here)")


def check_ci(root: Path) -> Check:
    wf = _read(root, ".github/workflows/ci.yml")
    if wf is None:
        return Check("ci", "fail", "CI workflow", ".github/workflows/ci.yml is missing")
    need = ("pytest", "vitest" if "vitest" in wf else "pnpm test", "ruff", "release-check")
    miss = [n for n in need if n not in wf]
    return Check("ci", "fail" if miss else "ok", "CI workflow", ("does not run: " + ", ".join(miss)) if miss else "runs pytest, the web tests, ruff and release-check")


def check_gitignore(root: Path) -> Check:
    gi = _read(root, ".gitignore") or ""
    lines = {ln.strip() for ln in gi.splitlines()}
    need = ("**/data/", "memory/*", "config.toml", "backups/", "*.pem", ".env", "/secrets/")
    miss = [n for n in need if n not in lines]
    return Check("gitignore", "fail" if miss else "ok", ".gitignore", ("missing entries: " + ", ".join(miss)) if miss else "personal-data paths are ignored")


# ---------------------------------------------------------------- the tools

Runner = Callable[[list[str], Path, int], tuple[int, str]]


def default_runner(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    """Run a local tool and return (exit code, the last lines of its output). No network is used by the tools themselves in this project."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]}: command not found"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-6:])
    return p.returncode, tail


def run_tools(root: Path, *, tests: bool, web: bool, runner: Runner) -> list[Check]:
    out: list[Check] = []
    plan = [("pytest", "Python tests", [sys.executable, "-m", "pytest", "-q", "-x"], 3600, tests),
            ("ruff", "Lint (ruff)", [sys.executable, "-m", "ruff", "check", "src", "tests"], 300, tests),
            ("vitest", "Web tests (vitest)", ["pnpm", "--dir", "web", "test"], 900, web),
            ("typecheck", "Web type check", ["pnpm", "--dir", "web", "typecheck"], 600, web)]
    for cid, title, cmd, timeout, enabled in plan:
        if not enabled:
            out.append(Check(cid, "skip", title, "skipped on request: this run is not release-grade"))
            continue
        code, tail = runner(cmd, root, timeout)
        out.append(Check(cid, "ok" if code == 0 else "fail", title, "passed" if code == 0 else f"exit {code}: " + tail.replace("\n", " | ")[:400]))
    return out


def release_checks(root: Path, *, tests: bool = True, web: bool = True, runner: Runner = default_runner, ci: bool = False,
                   terms: Optional[Path] = None) -> list[Check]:
    root = Path(root)
    files = hygiene.publishable_files(root)
    checks: list[Check] = []
    checks += check_license(root)
    checks.append(check_license_markers(root, files))
    checks += [check_version(root), check_web_built(root)]
    checks += check_hygiene(root, files, ci=ci, terms=terms)
    checks += check_metadata(root)
    checks += [check_docs(root), check_gitignore(root), check_docker(root), check_ci(root)]
    checks += run_tools(root, tests=tests, web=web, runner=runner)
    return checks


def cmd_release_check(a, cfg, *, runner: Runner = default_runner, out=print) -> None:
    root = Path(a.root).resolve() if getattr(a, "root", None) else home_mod.CHECKOUT_ROOT
    if not (root / "pyproject.toml").is_file():
        sys.exit("error: run `coach dev release-check` from a source checkout (no pyproject.toml found); --root DIR to point at one")
    checks = release_checks(root, tests=not a.skip_tests, web=not a.skip_web, runner=runner, ci=getattr(a, "ci", False))
    failed = [c for c in checks if c.status == "fail"]
    skipped = [c for c in checks if c.status == "skip"]
    if getattr(a, "json", False):
        out(json.dumps({"ok": not failed, "release_grade": not failed and not skipped, "checks": [c.to_dict() for c in checks]}, indent=2))
    else:
        mark = {"ok": "ok  ", "fail": "FAIL", "skip": "skip"}
        out(f"coach dev release-check (coach {__version__}, {root})")
        for c in checks:
            out(f"  [{mark[c.status]}] {c.title:<26}{c.detail}")
        if failed:
            out(f"\nNOT READY: {len(failed)} check(s) failed. See docs/release.md.")
        elif skipped:
            out(f"\nAll run checks passed, but {len(skipped)} were skipped: this is NOT a release-grade run.")
        else:
            out("\nREADY: every check passed.")
    if failed:
        sys.exit(1)


def cmd_hygiene(a, cfg, *, out=print) -> None:
    root = Path(a.root).resolve() if getattr(a, "root", None) else home_mod.CHECKOUT_ROOT
    tpath = hygiene.terms_path()
    if getattr(a, "build_terms", False):
        build_local_terms(cfg, tpath, out=out)
        return
    files = hygiene.publishable_files(root)
    problem = hygiene.terms_file_problem(tpath, root)
    index = None if problem else hygiene.TermIndex(hygiene.load_terms(tpath))
    hits = hygiene.scan(root, files, index)
    out(f"{len(files)} publishable file(s) scanned" + ("" if index is None else f" with {index.count} local terms"))
    for h in hits:
        out(f"  {h}")
    if problem and getattr(a, "ci", False):
        out(f"real-data rule SKIPPED (--ci): the local term file is {problem}; only the structural rules ran")
    elif problem:
        out(f"real-data rule NOT RUN: the local term file {tpath} is {problem}. Build it on the machine that holds the data: `coach dev hygiene --build-terms`")
    out("no hit" if not hits else f"{len(hits)} hit(s): replace the real values with invented ones (a hit never prints the value)")
    if hits or (problem and not getattr(a, "ci", False)):
        sys.exit(1)


def _series_amounts(con, cfg) -> list:
    """Expected amounts and price-history levels of every recurring series, in euros (best effort: an empty list when the analytics cannot run)."""
    try:
        from coach.analytics import api as analytics_api, pricechanges
        from coach.analytics.recurring import detect_recurring
        ds = analytics_api.build_dataset(con, cfg, None)
        out = []
        for s in detect_recurring(ds).series:
            out += [s.expected_amount_c / 100, s.amount_low_c / 100, s.amount_high_c / 100]
        for c in pricechanges.price_changes(ds).changes:
            out += [c.old_c / 100, c.new_c / 100]
        return out
    except Exception:                                                              # noqa: BLE001
        return []


def build_local_terms(cfg, path: Path, out=print) -> int:
    """`coach dev hygiene --build-terms`: derive the local term file from the database and memory this configuration points at. Prints counts only."""
    from coach.db import connect
    if hygiene.inside_repository(path):
        sys.exit(f"error: refusing to write the local term file inside the repository ({path}): point COACH_HYGIENE_TERMS outside it")
    con = connect(cfg, insecure=bool(getattr(cfg, "insecure_plaintext_db", False)), migrate=False)
    try:
        terms = hygiene.build_terms(con, cfg.memory_dir, eb_app_id=cfg.eb_app_id, extra_machine=hygiene.machine_terms(),
                                    extra_amounts=_series_amounts(con, cfg))
    finally:
        con.close()
    n = hygiene.write_terms(path, terms)
    out(f"wrote {n} term(s) to {path} (mode 0600; never commit or share it). Terms per category:")
    for cat in hygiene.CATEGORIES:
        if terms.get(cat):
            out(f"  {cat:<13}{len(terms[cat])}")
    return n


def register(sub, add) -> None:
    dp = sub.add_parser("dev", help="maintainer tools: release-check, hygiene", description="maintainer tools (run from a source checkout)")
    dsub = dp.add_subparsers(dest="dev_cmd", required=True, metavar="SUBCOMMAND")
    s = add(dsub, "release-check", cmd_release_check, "everything that must be true before publishing (licence, version, build, personal data, docs, docker, CI, tests); exit 1 if not ready")
    s.add_argument("--skip-tests", action="store_true", help="do not run pytest and ruff (the run is then not release-grade)")
    s.add_argument("--skip-web", action="store_true", help="do not run vitest and the TypeScript check (the run is then not release-grade)")
    s.add_argument("--json", action="store_true"); s.add_argument("--root", metavar="DIR", help="the checkout to check (default: this one)")
    s.add_argument("--ci", action="store_true", help="CI mode: the local real-data term file does not exist there, so that one rule is skipped (and reported as skipped)")
    s = add(dsub, "hygiene", cmd_hygiene, "scan every publishable file (not excluded by .gitignore) for personal data; never prints a value")
    s.add_argument("--root", metavar="DIR", help="the checkout to scan (default: this one)")
    s.add_argument("--build-terms", action="store_true", help="derive the LOCAL real-data term file from the database and memory this configuration points at (never prints a term)")
    s.add_argument("--ci", action="store_true", help="skip the real-data rule when there is no local term file (CI), and say so")
