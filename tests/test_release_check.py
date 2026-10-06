"""E13-4: `coach dev release-check`. A throwaway project built from the real files passes every check; remove the LICENSE (or leave an owner
placeholder, break the version, the build, the docs, the docker files...) and it fails with the reason. The real checkout must fail while no licence exists.
The tools (pytest, ruff, pnpm) are replaced by a recording fake: nothing is run for real."""
import json
import os
import re
import shutil
from argparse import Namespace
from pathlib import Path

import pytest

from coach import __version__, release as R

ROOT = Path(__file__).resolve().parents[1]
MARKERS = (R.LICENSE_MARKER, R.CONTACT_MARKER, R.REPO_MARKER)


class Tools:
    def __init__(self, fail=()):
        self.cmds, self.fail = [], set(fail)

    def __call__(self, cmd, cwd, timeout):
        self.cmds.append(cmd)
        name = "pytest" if "pytest" in cmd else "ruff" if "ruff" in cmd else "vitest" if cmd[-1] == "test" else "typecheck"
        return (1, "boom detail") if name in self.fail else (0, "")


def make_project(tmp_path):
    """A copy of the real project's release-relevant files, with the owner's decisions made (licence, contact, URL)."""
    p = tmp_path / "proj"
    for rel in ("pyproject.toml", "README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md", "LICENSE.choose.md", ".gitignore",
                "Dockerfile", "docker-compose.yml", ".dockerignore", ".github/workflows/ci.yml", "docs/architecture.md", "docs/release.md", "docs/docker.md",
                "docs/claude-settings.example.json", "src/coach/templates/memory/README.md", "src/coach/templates/memory/household.yaml",
                "src/coach/templates/memory/liabilities/_template.yaml", "src/coach/templates/memory/contracts/_template.yaml"):
        dest = p / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = (ROOT / rel).read_text()
        for m in MARKERS:
            text = text.replace(m, "decided")
        dest.write_text(text)
    (p / "LICENSE").write_text("MIT License\n\nCopyright (c) the owner\n\nPermission is hereby granted, free of charge, to any person obtaining a copy ...\n" * 2)
    pyproject = (p / "pyproject.toml").read_text()
    if "\nlicense = " not in pyproject:                                       # the real file carries the decisions since 2026-10-06
        pyproject = pyproject.replace('readme = "README.md"', 'readme = "README.md"\nlicense = "MIT"')
    if "[project.urls]" not in pyproject:
        pyproject += '\n[project.urls]\nHomepage = "https://example.org/coach"\n'
    (p / "pyproject.toml").write_text(pyproject)
    static = p / "src" / "coach" / "api" / "static"
    (static / "assets").mkdir(parents=True)
    (static / "assets" / "app.js").write_text("1")
    (static / "index.html").write_text('<script src="/assets/app.js"></script>')
    return p


def terms_file(tmp_path, *terms, mode=0o600):
    """A LOCAL term file of INVENTED terms (the real one never exists in a test)."""
    t = tmp_path / "local-terms.txt"
    t.write_text("# LOCAL ONLY\n" + "".join(f"{c}\t{x}\n" for c, x in (terms or (("name", "Zorbaxville Quillon"),))))
    os.chmod(t, mode)
    return t


def run(root, **kw):
    kw.setdefault("runner", Tools())
    if "terms" not in kw and not kw.get("ci"):
        kw["terms"] = terms_file(root.parent)
    return {c.id: c for c in R.release_checks(root, **kw)}


def failed(checks):
    return sorted(i for i, c in checks.items() if c.status == "fail")


def test_a_complete_project_passes_every_check(tmp_path):
    tools = Tools()
    checks = run(make_project(tmp_path), runner=tools)
    assert failed(checks) == [], {i: c.detail for i, c in checks.items() if c.status == "fail"}
    assert {c.status for c in checks.values()} == {"ok"}
    assert [c[:3] for c in tools.cmds if "pytest" in c or "ruff" in c] == [[R.sys.executable, "-m", "pytest"], [R.sys.executable, "-m", "ruff"]]
    assert ["pnpm", "--dir", "web", "test"] in tools.cmds and ["pnpm", "--dir", "web", "typecheck"] in tools.cmds


def test_it_fails_when_the_licence_is_missing(tmp_path):
    p = make_project(tmp_path)
    (p / "LICENSE").unlink()
    c = run(p)
    assert failed(c) == ["license.file"] and "LICENSE.choose.md" in c["license.file"].detail
    (p / "LICENSE.choose.md").unlink()                                      # the decision aid is optional once decided
    assert failed(run(p)) == ["license.file"]


def test_a_tiny_or_wrongly_named_licence_file_does_not_count(tmp_path):
    p = make_project(tmp_path)
    (p / "LICENSE").write_text("MIT")
    assert failed(run(p)) == ["license.file"]
    (p / "LICENSE").unlink()
    (p / "LICENSE.choose.md").write_text("x" * 500)                            # this is the explainer, not a licence
    assert failed(run(p)) == ["license.file"]
    (p / "COPYING").write_text("GNU AFFERO GENERAL PUBLIC LICENSE " * 10)
    assert failed(run(p)) == []


@pytest.mark.parametrize("marker,where", [(R.LICENSE_MARKER, "pyproject.toml"), (R.CONTACT_MARKER, "SECURITY.md"), (R.REPO_MARKER, "README.md")])
def test_an_owner_placeholder_left_in_a_publishable_file_fails(tmp_path, marker, where):
    p = make_project(tmp_path)
    (p / where).write_text((p / where).read_text() + f"\n# {marker}\n")
    c = run(p)
    assert failed(c) == ["license.todo"] and marker in c["license.todo"].detail and where in c["license.todo"].detail


def test_the_marker_in_a_git_ignored_or_exempt_file_is_not_a_blocker(tmp_path):
    p = make_project(tmp_path)
    (p / "LICENSE.choose.md").write_text(f"mentions {R.LICENSE_MARKER} on purpose\n")
    (p / "docs" / "release.md").write_text(f"{R.CONTACT_MARKER}\n")
    (p / "tests").mkdir()
    (p / "tests" / "t.py").write_text(f"# {R.LICENSE_MARKER}\n")
    assert failed(run(p)) == []


def test_version_checks(tmp_path):
    p = make_project(tmp_path)
    log = (p / "CHANGELOG.md").read_text()
    (p / "CHANGELOG.md").write_text(log.replace(f"## [{__version__}] - 2026-10-06", f"## [{__version__}]"))
    assert failed(run(p)) == ["version"] and "date" in run(p)["version"].detail
    (p / "CHANGELOG.md").write_text(log.replace(f"## [{__version__}]", "## [9.9.9]"))
    assert failed(run(p)) == ["version"] and "9.9.9" in run(p)["version"].detail
    (p / "CHANGELOG.md").write_text("## [Unreleased]\n\n- soon\n\n" + log)                 # an Unreleased section on top is skipped
    assert failed(run(p)) == []
    (p / "CHANGELOG.md").write_text("no sections")
    assert failed(run(p)) == ["version"]
    (p / "CHANGELOG.md").write_text(log)
    py = (p / "pyproject.toml").read_text()
    (p / "pyproject.toml").write_text(py.replace(f'version = "{__version__}"', 'version = "0.0.1"'))
    assert failed(run(p)) == ["version"] and "__version__" in run(p)["version"].detail
    (p / "pyproject.toml").write_text(py.replace(f'version = "{__version__}"', 'version = "x"'))
    assert failed(run(p)) == ["version"]


def test_the_web_build_must_exist_be_complete_and_fresh(tmp_path):
    p = make_project(tmp_path)
    static = p / "src" / "coach" / "api" / "static"
    (static / "assets" / "app.js").unlink()
    assert failed(run(p)) == ["web"] and "missing files" in run(p)["web"].detail
    (static / "assets" / "app.js").write_text("1")
    (p / "web" / "src").mkdir(parents=True)
    src = p / "web" / "src" / "App.tsx"
    src.write_text("x")
    import os
    os.utime(src, (static.stat().st_mtime + 100, static.stat().st_mtime + 100))
    os.utime(static / "index.html", (static.stat().st_mtime, static.stat().st_mtime))
    assert failed(run(p)) == ["web"] and "newer" in run(p)["web"].detail
    shutil.rmtree(static)
    assert failed(run(p)) == ["web"] and "missing" in run(p)["web"].detail


def test_a_local_term_anywhere_publishable_fails_but_in_an_ignored_path_does_not(tmp_path):
    p = make_project(tmp_path)
    (p / "memory").mkdir()
    (p / "memory" / "profile.md").write_text("I live with Zorbaxville Quillon\n")             # memory/* is ignored
    assert failed(run(p)) == []
    (p / "docs" / "notes.md").write_text("I live with Zorbaxville Quillon\n")
    c = run(p)
    assert failed(c) == ["hygiene"] and "docs/notes.md:1" in c["hygiene"].detail and "real-data:name#1" in c["hygiene"].detail and "orbax" not in c["hygiene"].detail


def test_a_missing_local_term_file_fails_the_release_check_it_never_passes_silently(tmp_path):
    p = make_project(tmp_path)
    c = run(p, terms=tmp_path / "does-not-exist.txt")
    assert failed(c) == ["hygiene.local"] and "missing" in c["hygiene.local"].detail and "--build-terms" in c["hygiene.local"].detail
    assert c["hygiene"].status == "ok" and "structural rules only" in c["hygiene"].detail          # and says so


def test_an_empty_or_loose_term_file_fails_too(tmp_path):
    p = make_project(tmp_path)
    empty = tmp_path / "e.txt"
    empty.write_text("# nothing\n")
    os.chmod(empty, 0o600)
    assert failed(run(p, terms=empty)) == ["hygiene.local"] and "empty" in run(p, terms=empty)["hygiene.local"].detail
    loose = terms_file(tmp_path, mode=0o644)
    assert failed(run(p, terms=loose)) == ["hygiene.local"] and "readable by other users" in run(p, terms=loose)["hygiene.local"].detail


def test_ci_mode_skips_only_the_real_data_rule_and_says_so(tmp_path, capsys):
    p = make_project(tmp_path)
    c = run(p, ci=True, terms=tmp_path / "does-not-exist.txt")
    assert failed(c) == [] and c["hygiene.local"].status == "skip" and "--ci" in c["hygiene.local"].detail and "NOT run" in c["hygiene.local"].detail
    (p / "SECURITY.md").write_text((p / "SECURITY.md").read_text() + "\nkey " + "-----BEGIN " + "PRIVATE KEY-----\n")           # the structural rules still run in CI
    assert failed(run(p, ci=True, terms=tmp_path / "x.txt")) == ["hygiene"]
    q = make_project(tmp_path / "q")
    R.cmd_release_check(Namespace(root=str(q), skip_tests=False, skip_web=False, json=True, ci=True), None, runner=Tools())
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["release_grade"] is False


def test_the_sdist_member_list_is_scanned_and_honours_the_excludes(tmp_path):
    p = make_project(tmp_path)
    (p / "docs" / "research").mkdir()
    (p / "docs" / "research" / "n.md").write_text("fine\n")
    (p / "evals").mkdir()
    (p / "evals" / "q.yaml").write_text("fine\n")
    (p / "tests").mkdir()
    (p / "tests" / "t.py").write_text("x = 1\n")
    files = R.hygiene.publishable_files(p)
    members = {m.relative_to(p).as_posix() for m in R.sdist_members(p, files)}
    assert "tests/t.py" in members and "pyproject.toml" in members and "docs/architecture.md" in members
    assert "docs/research/n.md" not in members and "evals/q.yaml" not in members
    assert any(m.startswith("src/coach/api/static/") for m in members)                         # the build artifact is part of the sdist
    (p / "src" / "coach" / "api" / "static" / "assets" / "app.js").write_text("// Zorbaxville Quillon\n")       # gitignored, but shipped: scanned
    c = run(p)
    assert failed(c) == ["hygiene"] and "src/coach/api/static/assets/app.js:1" in c["hygiene"].detail


def test_a_built_sdist_and_wheel_are_scanned_member_by_member(tmp_path):
    import tarfile
    import zipfile
    p = make_project(tmp_path)
    (p / "dist").mkdir()
    with zipfile.ZipFile(p / "dist" / "x-1.0-py3-none-any.whl", "w") as z:
        z.writestr("coach/a.py", "fine\n")
        z.writestr("coach/b.txt", "lives in Zorbaxville Quillon\n")
    (tmp_path / "inner.txt").write_text("also Zorbaxville Quillon\n")
    with tarfile.open(p / "dist" / "x-1.0.tar.gz", "w:gz") as t:
        t.add(tmp_path / "inner.txt", arcname="x-1.0/inner.txt")
    c = run(p)
    assert failed(c) == ["hygiene"]
    assert "x-1.0-py3-none-any.whl:coach/b.txt:1" in c["hygiene"].detail or "x-1.0.tar.gz:x-1.0/inner.txt:1" in c["hygiene"].detail


def test_metadata_templates_docs_gitignore_docker_and_ci_checks(tmp_path):
    p = make_project(tmp_path)
    (p / "pyproject.toml").write_text((p / "pyproject.toml").read_text().replace('keywords = [', 'x_keywords = ['))
    assert "metadata" in failed(run(p))
    p = make_project(tmp_path / "b")
    (p / "pyproject.toml").write_text((p / "pyproject.toml").read_text().replace("[tool.hatch.build.targets.wheel.force-include]", "[tool.hatch.build.targets.wheel.x]"))
    assert failed(run(p)) == ["package-data"]
    p = make_project(tmp_path / "c")
    (p / "src/coach/templates/memory/household.yaml").unlink()
    assert failed(run(p)) == ["templates"]
    p = make_project(tmp_path / "d")
    (p / "pyproject.toml").write_text(re.sub(r"\[project\.urls\]\n(?:[A-Za-z]+ = \"[^\"]*\"\n)+", "", (p / "pyproject.toml").read_text()))
    assert failed(run(p)) == ["urls"]
    p = make_project(tmp_path / "e")
    (p / "docs/architecture.md").unlink()
    assert failed(run(p)) == ["docs"]
    (p / "docs/architecture.md").write_text("x")
    (p / "docs/claude-settings.example.json").write_text("{not json")
    assert failed(run(p)) == ["docs"] and "JSON" in run(p)["docs"].detail
    p = make_project(tmp_path / "f")
    (p / ".gitignore").write_text("*.pyc\n")
    assert failed(run(p)) == ["gitignore"]
    p = make_project(tmp_path / "g")
    (p / "Dockerfile").write_text((p / "Dockerfile").read_text().replace("USER 10001:10001", "USER root"))
    c = run(p)
    assert failed(c) == ["docker"] and "DF001" in c["docker"].detail
    p = make_project(tmp_path / "i")
    (p / "docs/docker.md").write_text((p / "docs/docker.md").read_text() + "\n    docker run -p 8765:8765 image\n")
    c = run(p)
    assert failed(c) == ["docker"] and "DC008" in c["docker"].detail
    p = make_project(tmp_path / "h")
    (p / ".github/workflows/ci.yml").write_text("name: x\non: push\njobs: {}\n")
    assert failed(run(p)) == ["ci"]
    (p / ".github/workflows/ci.yml").unlink()
    assert failed(run(p)) == ["ci"]


def test_a_failing_tool_fails_with_its_output_and_skips_are_reported_as_not_release_grade(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("COACH_HYGIENE_TERMS", str(terms_file(tmp_path)))
    p = make_project(tmp_path)
    c = run(p, runner=Tools(fail={"pytest"}))
    assert failed(c) == ["pytest"] and "exit 1" in c["pytest"].detail and "boom detail" in c["pytest"].detail
    tools = Tools()
    c = run(p, tests=False, web=False, runner=tools)
    assert tools.cmds == [] and {i for i, x in c.items() if x.status == "skip"} == {"pytest", "ruff", "vitest", "typecheck"}
    a = Namespace(root=str(p), skip_tests=True, skip_web=True, json=False)
    R.cmd_release_check(a, None, runner=tools)
    assert "NOT a release-grade run" in capsys.readouterr().out
    a = Namespace(root=str(p), skip_tests=True, skip_web=False, json=True)
    R.cmd_release_check(a, None, runner=tools)
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["release_grade"] is False


def test_the_command_exits_1_and_says_not_ready_when_anything_fails(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("COACH_HYGIENE_TERMS", str(terms_file(tmp_path)))
    p = make_project(tmp_path)
    (p / "LICENSE").unlink()
    with pytest.raises(SystemExit) as e:
        R.cmd_release_check(Namespace(root=str(p), skip_tests=False, skip_web=False, json=False), None, runner=Tools())
    out = capsys.readouterr().out
    assert e.value.code == 1 and "NOT READY" in out and "[FAIL] LICENSE file" in out
    (p / "LICENSE").write_text("MIT License " * 20)
    R.cmd_release_check(Namespace(root=str(p), skip_tests=False, skip_web=False, json=False), None, runner=Tools())
    assert "READY: every check passed" in capsys.readouterr().out


def test_it_refuses_to_run_outside_a_checkout(tmp_path):
    with pytest.raises(SystemExit) as e:
        R.cmd_release_check(Namespace(root=str(tmp_path), skip_tests=True, skip_web=True, json=False), None)
    assert "source checkout" in str(e.value)


def test_the_default_runner_reports_a_missing_command_and_a_timeout_without_raising(tmp_path):
    assert R.default_runner(["definitely-not-a-command-xyz"], tmp_path, 5)[0] == 127
    code, tail = R.default_runner([R.sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, 1)
    assert code == 124 and "timed out" in tail
    code, tail = R.default_runner([R.sys.executable, "-c", "print('one'); import sys; sys.exit(3)"], tmp_path, 20)
    assert code == 3 and "one" in tail


def test_the_real_checkout_is_blocked_while_the_licence_is_undecided():
    """The task's acceptance test: it must FAIL now because no licence has been chosen. Once the owner adds LICENSE and removes the
    placeholders this test has nothing left to prove and skips."""
    if any(p.name.lower() in R.LICENSE_NAMES for p in ROOT.iterdir() if p.is_file()):
        pytest.skip("a LICENSE exists: the owner has decided")
    checks = {c.id: c for c in R.release_checks(ROOT, tests=False, web=False, ci=True)}
    assert checks["license.file"].status == "fail" and "LICENSE.choose.md" in checks["license.file"].detail
    assert checks["license.todo"].status == "fail" and R.LICENSE_MARKER in checks["license.todo"].detail
    with pytest.raises(SystemExit) as e:
        R.cmd_release_check(Namespace(root=None, skip_tests=True, skip_web=True, json=False), None)
    assert e.value.code == 1
    # and what does not depend on the owner is already fine (structural hygiene, version, docs, docker, CI, metadata); the real-data rule needs the owner's local file
    for ok in ("hygiene", "version", "docs", "gitignore", "docker", "ci", "metadata", "package-data", "templates"):
        assert checks[ok].status == "ok", (ok, checks[ok].detail)


def test_the_dev_commands_are_registered():
    from coach.cli import build_parser
    p = build_parser()
    assert p.parse_args(["dev", "release-check", "--skip-tests"]).fn is R.cmd_release_check
    assert p.parse_args(["dev", "hygiene"]).fn is R.cmd_hygiene


def test_the_hygiene_command_reports_hits_without_values_and_needs_the_local_file(tmp_path, capsys, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.md").write_text("Zorbaxville Quillon\n")
    (proj / ".gitignore").write_text("")
    monkeypatch.setenv("COACH_HYGIENE_TERMS", str(terms_file(tmp_path)))
    cfg = type("C", (), {"root": tmp_path})()
    with pytest.raises(SystemExit) as e:
        R.cmd_hygiene(Namespace(root=str(proj), build_terms=False, ci=False), cfg)
    out = capsys.readouterr().out
    assert e.value.code == 1 and "a.md:1  [real-data:name#1]" in out and "orbax" not in out.lower()
    monkeypatch.setenv("COACH_HYGIENE_TERMS", str(tmp_path / "missing.txt"))
    (proj / "a.md").write_text("fine\n")
    with pytest.raises(SystemExit) as e:
        R.cmd_hygiene(Namespace(root=str(proj), build_terms=False, ci=False), cfg)
    assert e.value.code == 1 and "NOT RUN" in capsys.readouterr().out
    R.cmd_hygiene(Namespace(root=str(proj), build_terms=False, ci=True), cfg)                     # --ci: skipped, said so, exit 0
    assert "SKIPPED (--ci)" in capsys.readouterr().out
