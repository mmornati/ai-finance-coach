"""E13-1 / E13-4: packaging metadata, the shipped templates, the CI workflow, the Claude Code permission template, the README promises.
Static: nothing is built or installed here (a wheel build needs the network for its build backend)."""
import json
import tomllib
from pathlib import Path

import yaml

from coach import __version__, config as config_mod, home as H

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
PROJECT = PYPROJECT["project"]


def test_the_package_metadata_is_complete():
    assert PROJECT["name"] == "ai-finance-coach" and PROJECT["version"] == __version__
    assert PROJECT["requires-python"] == ">=3.11" and PROJECT["readme"] == "README.md" and (ROOT / "README.md").is_file()
    assert PROJECT["scripts"] == {"coach": "coach.cli:main"}
    assert len(PROJECT["description"]) > 40 and len(PROJECT["keywords"]) >= 5
    cl = PROJECT["classifiers"]
    assert "Programming Language :: Python :: 3.13" in cl and any(c.startswith("Topic :: Office/Business :: Financial") for c in cl)
    assert not any(c.startswith("License ::") for c in cl) or "license" in PROJECT                  # a licence classifier only once the licence is decided
    assert PYPROJECT["build-system"]["build-backend"] == "hatchling.build"


def test_the_licence_is_unset_until_the_owner_decides_and_the_marker_says_so():
    text = (ROOT / "pyproject.toml").read_text()
    if "license" not in PROJECT:
        assert "TODO" + "(license)" in text and "LICENSE.choose.md" in text          # unset on purpose, with the pointer
    else:
        assert PROJECT["license"] and "TODO" + "(license)" not in text


def test_the_wheel_ships_the_web_app_the_config_example_and_the_import_profiles():
    wheel = PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["src/coach"]
    assert "src/coach/api/static/**" in wheel["artifacts"]                           # git-ignored, so it must be listed explicitly
    fi = wheel["force-include"]
    assert fi == {"config.example.toml": "coach/templates/config.example.toml", "config/import_profiles": "coach/templates/import_profiles"}
    for src in fi:
        assert (ROOT / src).exists()
    sdist = PYPROJECT["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert "/web/src" in sdist["include"] and "/src" in sdist["include"] and "src/coach/api/static/**" in sdist["artifacts"]
    assert "/docs/research" in sdist["exclude"] and "/evals" in sdist["exclude"] and "/docs/research" not in sdist["include"]


def test_the_data_files_the_program_needs_live_inside_the_package():
    pkg = ROOT / "src" / "coach"
    assert (pkg / "classify" / "taxonomy.yaml").is_file() and (pkg / "classify" / "rules.yaml").is_file() and (pkg / "classify" / "taxonomy_equivalence.yaml").is_file()
    assert sorted(p.name for p in (pkg / "migrations").iterdir() if p.suffix in (".sql", ".py"))[0].startswith("0001")
    assert not (ROOT / ".gitignore").read_text().count("migrations")
    assert H.memory_template().is_dir() and (H.memory_template() / "household.yaml").is_file()


def test_templates_resolve_in_a_checkout_and_from_the_package(monkeypatch, tmp_path):
    assert H.in_checkout() and H.config_template() == ROOT / "config.example.toml"
    assert H.import_profiles_template() == ROOT / "config" / "import_profiles"
    fake = tmp_path / "templates"
    (fake / "import_profiles").mkdir(parents=True)
    (fake / "config.example.toml").write_text("x = 1\n")
    monkeypatch.setattr(H, "TEMPLATES_DIR", fake)
    assert H.config_template() == fake / "config.example.toml" and H.import_profiles_template() == fake / "import_profiles"


def test_the_template_memory_has_no_household_data_and_no_comment_free_surprises():
    files = H.template_files(H.memory_template())
    assert {f.as_posix() for f in files} == {"README.md", "profile.md", "preferences.md", "events.md", "household.yaml", "assets.yaml", "categorization.yaml",
                                              "liabilities/_template.yaml", "contracts/_template.yaml"}
    assert yaml.safe_load((H.memory_template() / "household.yaml").read_text()) == {"members": []}
    assert yaml.safe_load((H.memory_template() / "assets.yaml").read_text()) == {"assets": []}
    assert yaml.safe_load((H.memory_template() / "categorization.yaml").read_text()) == {"annotations": []}
    # the packaged templates are the generic version of the repository's own template files
    assert (H.memory_template() / "liabilities" / "_template.yaml").read_text().count("\n") == (ROOT / "memory" / "liabilities" / "_template.yaml").read_text().count("\n") \
        or not (ROOT / "memory" / "liabilities" / "_template.yaml").exists()


def test_an_installed_package_has_its_own_default_home(monkeypatch, tmp_path):
    monkeypatch.setattr(H, "in_checkout", lambda: False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert config_mod.find_root() == (tmp_path / ".ai-finance-coach").resolve()
    monkeypatch.setenv("COACH_HOME", str(tmp_path / "elsewhere"))
    assert config_mod.find_root() == (tmp_path / "elsewhere").resolve()
    monkeypatch.delenv("COACH_HOME")
    (tmp_path / "config.toml").write_text('data_dir = "d"\n')                       # a configuration in the working folder still wins
    assert config_mod.find_root() == tmp_path.resolve()


def test_the_ci_workflow_runs_every_gate_and_the_release_check_is_not_allowed_to_fail_silently():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    jobs = wf["jobs"]
    assert set(jobs) == {"python", "web", "release-check"}
    runs = lambda j: " ".join(s.get("run", "") for s in jobs[j]["steps"])                    # noqa: E731
    assert "pytest" in runs("python") and "ruff check src tests" in runs("python")
    assert "pnpm typecheck" in runs("web") and "pnpm test" in runs("web") and "pnpm build" in runs("web")
    assert "coach dev release-check" in runs("release-check") and "pnpm build" in runs("release-check")
    assert "continue-on-error" not in text.replace("Do not mark it continue-on-error", "")
    assert jobs["release-check"]["needs"] == ["python", "web"] and wf["permissions"] == {"contents": "read"}


def test_the_ruff_policy_is_correctness_only_and_matches_what_ci_runs():
    lint = PYPROJECT["tool"]["ruff"]["lint"]
    assert set(lint["select"]) == {"E9", "F63", "F7", "F82", "F811"} and lint["per-file-ignores"] == {"tests/*": ["F811"]}


def test_the_permission_rules_template_covers_every_category_of_docs_security_and_holds_no_personal_path():
    raw = (ROOT / "docs" / "claude-settings.example.json").read_text()
    deny = json.loads(raw)["permissions"]["deny"]
    ask = json.loads(raw)["permissions"]["ask"]
    joined = "\n".join(deny)
    for needle in ("memory accept", "purge-history", "proposals.accept", ".history.git", "proposal_key", "Edit(/memory/**)",              # memory integrity
                   "ui-session.key", "ui-state.json", "ui-login.json", "ui-revoked.json", "Edit(/data/**)",                              # the app's state
                   "coach ui", "coach.api", "LoginTokens", "session/exchange", ":8765", "127.1", "[::1]", "0x7f", "login#t=",            # driving the app
                   "--yes", "Bash(script *)",                                                                                         # confirmation bypass
                   "decisions confirm", "subs contact", "test-channel", "alerts digest*--send",                                         # decisions and outside contact
                   "coach wipe", "--plain", "--decrypt", "find-generic-password", "keyring", "delete_secret", "set-secret",              # E11 additions
                   "backups", ".tar.enc", ".zip.enc", "*.pem", "config.toml", "/run/secrets"):
        assert needle in joined, needle
    asks = "\n".join(ask)
    for needle in ("doc extract", "--send", "memory revert", "memory reject", "--fix-permissions"):
        assert needle in asks, needle
    assert "/Users/" not in raw and "/home/" not in raw
    assert len(deny) == len(set(deny)) and not set(deny) & set(ask)
    assert set(json.loads(raw)) == {"permissions"} and set(json.loads(raw)["permissions"]) == {"deny", "ask"}


def test_the_documentation_set_exists_and_links_resolve():
    import re
    docs = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md", "LICENSE.choose.md", "docs/architecture.md", "docs/release.md",
            "docs/docker.md", "docs/reference.md"]
    for d in docs:
        text = (ROOT / d).read_text()
        for target in re.findall(r"\]\((?!https?:|#|mailto:)([^)\s]+)\)", text):
            path = (ROOT / d).parent / target.split("#")[0]
            assert path.exists(), f"{d} links to {target}"


def test_the_readme_promises_what_exists():
    r = (ROOT / "README.md").read_text()
    for cmd in ("coach init", "coach doctor", "coach setup enablebanking", "coach setup", "coach ui", "coach schedule install", "coach schedule loop", "coach privacy status",
                "coach privacy report", "coach security audit"):
        assert cmd in r, cmd
    from coach.cli import build_parser
    p = build_parser()
    for argv in (["init"], ["doctor"], ["setup"], ["setup", "enablebanking"], ["schedule", "loop"], ["dev", "release-check"], ["dev", "hygiene"]):
        assert p.parse_args(argv).fn
    for needle in ("docs/claude-settings.example.json", "docs/architecture.md", "docs/docker.md", "LICENSE.choose.md", "not financial advice", "Screenshots"):
        assert needle.lower() in r.lower(), needle


def test_claude_md_and_the_reference_agree_on_the_web_build_not_being_committed():
    for rel in ("CLAUDE.md", "docs/reference.md"):
        text = (ROOT / rel).read_text()
        assert "static/` is committed" not in text and "(committed: rebuild" not in text, rel
        assert "NOT committed" in text, rel
    assert "src/coach/api/static/" in (ROOT / ".gitignore").read_text()
