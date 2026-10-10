"""E13-1: static checks of the Dockerfile, docker-compose.yml and .dockerignore. No docker daemon, no build, no network: the files are
parsed and judged by coach.dockerlint (hadolint-like rules). Each rule is also shown to FIRE on a bad input, so the lint cannot rot into
accepting everything."""
import re
import stat
from pathlib import Path

import pytest
import yaml

from coach import dockerlint as L

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()
IGNORE = (ROOT / ".dockerignore").read_text()


def rules(findings):
    return {f.rule for f in findings}


# ---------------------------------------------------------------- the real files pass

def test_the_real_dockerfile_compose_and_dockerignore_pass_every_rule():
    assert L.lint_all(DOCKERFILE, COMPOSE, IGNORE) == []


def test_the_dockerfile_runs_as_a_numeric_non_root_user_with_a_healthcheck_and_exec_form():
    ins = L._instructions(DOCKERFILE)
    last_user = [a for _, h, a, _ in ins if h == "USER"][-1]
    assert re.fullmatch(r"\d+:\d+", last_user) and not last_user.startswith("0")        # numeric: runAsNonRoot works in Kubernetes
    assert any(h == "HEALTHCHECK" for _, h, _, _ in ins)
    assert [a for _, h, a, _ in ins if h == "ENTRYPOINT"][0].startswith("[")
    assert not any(h == "ADD" for _, h, _, _ in ins)


def test_every_base_image_is_pinned_by_a_digest_or_carries_the_placeholder_comment():
    froms = [(a, c) for _, h, a, c in L._instructions(DOCKERFILE) if h == "FROM"]
    assert len(froms) == 4
    for image, comments in froms:
        assert L.DIGEST.search(image) or any("PIN-DIGEST" in c for c in comments), image
        assert ":" in image.split()[0] and not image.split()[0].endswith(":latest")


def test_the_web_build_stays_out_of_the_runtime_stage():
    final = DOCKERFILE[DOCKERFILE.rindex("FROM "):]
    assert "node" not in final.split("\n", 1)[0] and "pnpm" not in final and "COPY --from=web" not in final
    assert "COPY --from=build /opt/venv /opt/venv" in final


def test_the_container_has_no_keychain_and_the_claude_cli_is_opt_in_pinned_and_verified():
    assert "COACH_SECRETS_BACKEND=file" in DOCKERFILE and "COACH_SECRETS_DIR=/run/secrets" in DOCKERFILE and "COACH_IN_CONTAINER=1" in DOCKERFILE
    assert "keyring" not in DOCKERFILE.lower()
    cli = DOCKERFILE[DOCKERFILE.index("AS claude-cli"):DOCKERFILE.rindex("FROM ")]
    assert "ARG WITH_CLAUDE_CODE=0" in cli                                           # off by default: the default image has no claude
    assert re.search(r"ARG CLAUDE_CODE_VERSION=\d+\.\d+\.\d+\n", cli)                  # an exact version, never latest
    assert cli.count("ARG CLAUDE_CODE_INTEGRITY_") == 2 and "integrity mismatch" in cli and "curl" not in cli
    final = DOCKERFILE[DOCKERFILE.rindex("FROM "):]
    assert "COPY --from=claude-cli /out /opt/claude" in final and "/opt/claude/bin" in final
    assert "DISABLE_AUTOUPDATER=1" in final and "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1" in final
    assert "node" not in final.split("\n", 1)[0] and "npm" not in final                 # one native binary, no Node in the runtime


def test_compose_publishes_the_web_app_on_the_host_loopback_only():
    doc = yaml.safe_load(COMPOSE)
    ports = [p for svc in doc["services"].values() for p in svc.get("ports", [])]
    assert ports == ["127.0.0.1:8765:8765"]
    assert "0.0.0.0" not in COMPOSE.replace('host: "0.0.0.0"', "")


def test_compose_services_are_locked_down_and_use_secret_files_not_environment_values():
    doc = yaml.safe_load(COMPOSE)
    assert set(doc["services"]) == {"coach", "scheduler"} and doc["services"]["scheduler"]["profiles"] == ["scheduler"]
    for name, svc in doc["services"].items():
        assert svc["init"] is True and svc["read_only"] is True and svc["cap_drop"] == ["ALL"] and "no-new-privileges:true" in svc["security_opt"], name
        assert not str(svc["user"]).startswith(("0", "root")) and "/tmp" in svc["tmpfs"], name
        assert {"db_key", "backup_key", "proposal_key"} <= set(svc["secrets"]), name
        assert any(v.endswith(":/data") for v in svc["volumes"]) and any(v.endswith(":/memory") for v in svc["volumes"]) \
            and any(v.endswith(":/config") for v in svc["volumes"]), name
    assert all("file" in v for v in doc["secrets"].values()) and set(doc["secrets"]) == {"db_key", "backup_key", "proposal_key"}
    assert doc["services"]["scheduler"]["command"] == ["schedule", "loop"] and doc["services"]["scheduler"]["healthcheck"] == {"disable": True}


def test_the_files_the_dockerfile_copies_exist_in_the_repository():
    for _, h, a, _ in L._instructions(DOCKERFILE):
        if h != "COPY" or "--from=" in a:
            continue
        *srcs, _dest = [x for x in a.split() if not x.startswith("--")]
        for s in srcs:
            assert (ROOT / s).exists(), f"COPY source {s} does not exist"


def test_the_dockerignore_does_not_exclude_what_the_build_needs():
    ignored = {ln.strip().rstrip("/") for ln in IGNORE.splitlines() if ln.strip() and not ln.startswith("#")}
    needed = {"pyproject.toml", "uv.lock", "README.md", "config.example.toml", "config/import_profiles", "src", "web", "docker", "Dockerfile"}
    assert not needed & ignored
    assert {"data", "memory", "secrets", "coach-home", "backups", "config.toml", "*.pem", ".env"} <= ignored


def test_the_entrypoint_creates_the_home_non_interactively_and_hands_over_with_exec():
    sh = (ROOT / "docker" / "entrypoint.sh").read_text()
    assert sh.startswith("#!/bin/sh") and "set -eu" in sh
    assert "coach init" in sh and "--non-interactive" in sh and "exec coach" in sh and "--yes" not in sh
    assert (ROOT / "docker" / "entrypoint.sh").stat().st_size < 2000
    # COACH_CONFIG (set by the image) names the file init creates and every command loads it first: init must start without it
    assert "env -u COACH_CONFIG coach init" in sh


# ---------------------------------------------------------------- the web app inside the container

def _cfg(**over):
    from coach.config import load_config
    cfg = load_config(None, env={})
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def test_all_interfaces_need_a_real_container_AND_the_explicit_setting_every_refusal_path(monkeypatch):
    from coach.api import server
    from coach.config import ConfigError
    monkeypatch.setattr(server, "container_marker", lambda: False)
    with pytest.raises(ConfigError):
        server.check_bind(_cfg(), "0.0.0.0")                                       # a host, no setting
    with pytest.raises(ConfigError) as e:
        server.check_bind(_cfg(ui_container_bind=True), "0.0.0.0")                   # the setting on a host: refused, and it says why
    assert "not running in a container" in str(e.value)
    monkeypatch.setenv("COACH_IN_CONTAINER", "1")                                  # an environment variable alone proves nothing
    with pytest.raises(ConfigError):
        server.check_bind(_cfg(), "0.0.0.0")
    with pytest.raises(ConfigError):
        server.check_bind(_cfg(ui_container_bind=True), "::")
    monkeypatch.setattr(server, "container_marker", lambda: True)
    with pytest.raises(ConfigError) as e:
        server.check_bind(_cfg(), "0.0.0.0")                                       # a real container but no setting (an old configuration)
    assert "container_bind = true" in str(e.value)
    with pytest.raises(ConfigError):
        server.check_bind(_cfg(ui_container_bind=True), "192.168.1.20")             # a specific address is never accepted this way
    server.check_bind(_cfg(ui_container_bind=True), "0.0.0.0")                       # both: allowed
    server.check_bind(_cfg(ui_container_bind=True), "::")
    server.check_bind(_cfg(), "127.0.0.1")                                          # loopback always
    monkeypatch.delenv("COACH_IN_CONTAINER")
    server.check_bind(_cfg(ui_container_bind=True), "0.0.0.0")                       # the environment variable is not needed either


def test_the_container_marker_is_environment_independent(tmp_path, monkeypatch):
    from coach import home as H
    assert H.on_container_filesystem(paths=(str(tmp_path / "nope"),), cgroup=str(tmp_path / "nocg")) is False
    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    assert H.on_container_filesystem(paths=(str(marker),), cgroup=str(tmp_path / "nocg")) is True
    cg = tmp_path / "cgroup"
    cg.write_text("0::/\n")
    assert H.on_container_filesystem(paths=(), cgroup=str(cg)) is False
    cg.write_text("12:cpu:/docker/abc123\n")
    assert H.on_container_filesystem(paths=(), cgroup=str(cg)) is True
    cg.write_text("1:name=systemd:/kubepods/burstable/pod1\n")
    assert H.on_container_filesystem(paths=(), cgroup=str(cg)) is True
    monkeypatch.setenv("COACH_IN_CONTAINER", "1")
    assert H.on_container_filesystem(paths=(), cgroup=str(tmp_path / "nocg")) is False


def test_only_the_images_init_writes_the_container_bind_setting(tmp_path, monkeypatch):
    from coach import config as config_mod
    from coach.setup import init as I
    monkeypatch.setenv("COACH_SECRETS_BACKEND", "file")
    monkeypatch.setenv("COACH_SECRETS_DIR", str(tmp_path / "secrets"))
    I.run_init(tmp_path / "c", container=True, interactive=False, out=lambda *a: None)
    I.run_init(tmp_path / "n", container=False, interactive=False, out=lambda *a: None)
    assert config_mod.load_config(tmp_path / "c" / "config.toml", env={}).ui_container_bind is True
    assert config_mod.load_config(tmp_path / "n" / "config.toml", env={}).ui_container_bind is False
    assert "container_bind = false" in (ROOT / "config.example.toml").read_text()


def _run_ui(monkeypatch, tmp_path, cfg, tty):
    """cmd_ui with the server and the app stubbed out: what it prints and writes."""
    import sys
    from types import SimpleNamespace
    import uvicorn
    from coach.api import app as app_mod, server
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "create_app", lambda *a, **k: SimpleNamespace(state=SimpleNamespace(static=tmp_path)))
    monkeypatch.setattr(server, "container_marker", lambda: True)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "index.html").write_text("x")

    class Out:
        def __init__(self):
            self.buf = []

        def write(self, t):
            self.buf.append(t)
            return len(t)

        def flush(self):
            pass

        def reconfigure(self, **k):
            pass

        def isatty(self):
            return tty
    out, err = Out(), Out()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    server.cmd_ui(SimpleNamespace(host="0.0.0.0", port=8765, rotate_session_key=False, login_link=False, dev=False, insecure=True, no_browser=True), cfg)
    return "".join(out.buf), "".join(err.buf)


def test_in_container_mode_it_warns_loudly_and_keeps_the_login_link_out_of_the_logs(tmp_path, monkeypatch, cfg):
    cfg.ui_container_bind = True
    out, err = _run_ui(monkeypatch, tmp_path / "s", cfg, tty=False)
    assert "WARNING" in err and "127.0.0.1" in err and "published" in err
    assert "/login#t=" not in out and "NOT printed" in out and "docker compose exec coach coach" in out
    link = cfg.data_dir / "login-link.txt"
    assert stat.S_IMODE(link.stat().st_mode) == 0o600 and "/login#t=" in link.read_text()
    out2, _ = _run_ui(monkeypatch, tmp_path / "s2", cfg, tty=True)                        # a terminal is private: the link is printed
    assert "/login#t=" in out2


def test_behind_a_proxy_in_a_container_the_login_link_stays_out_of_the_logs_too(tmp_path, monkeypatch, cfg):
    """allow_remote (the Tailscale / authentik recipes) is not the loopback container mode, but a container is a container: no TTY,
    no link in `docker logs` (issue #37)."""
    cfg.ui_container_bind = False
    cfg.ui_allow_remote, cfg.ui_remote_tls_ack, cfg.ui_allowed_hosts = True, True, ("coach.example.test",)
    out, err = _run_ui(monkeypatch, tmp_path / "s", cfg, tty=False)
    assert "/login#t=" not in out and "NOT printed" in out
    link = cfg.data_dir / "login-link.txt"
    assert stat.S_IMODE(link.stat().st_mode) == 0o600 and link.read_text().startswith("https://coach.example.test/login#t=")
    assert "published" not in err                                                          # that warning is the loopback container mode's
    out2, _ = _run_ui(monkeypatch, tmp_path / "s2", cfg, tty=True)                        # a terminal is private: the link is printed
    assert "https://coach.example.test/login#t=" in out2


def test_without_the_container_mode_nothing_changes(tmp_path, monkeypatch, cfg):
    cfg.ui_container_bind = False
    from coach.config import ConfigError
    with pytest.raises(ConfigError):
        _run_ui(monkeypatch, tmp_path / "s", cfg, tty=False)
    assert not (cfg.data_dir / "login-link.txt").exists()


def test_compose_dockerfile_and_docs_publish_on_the_loopback_only_DC008():
    for rel in ("docker-compose.yml", "README.md", "docs/docker.md", "docs/ui.md"):
        assert L.lint_port_examples((ROOT / rel).read_text(), rel) == [], rel
    docs = (ROOT / "docs" / "docker.md").read_text()
    assert "-p 127.0.0.1:8765:8765" in docs and "docker logs" in docs and "login-link.txt" in docs


@pytest.mark.parametrize("bad", [
    "docker compose run --rm -p 8443:8443 coach connect", "docker run -p 0.0.0.0:8443:8443 img", 'ports:\n  - "8443:8443"',
    "docker run -p 8765:8765 img", "docker run --publish 8765:8765 img", "docker run -p 0.0.0.0:8765:8765 img", "docker run -p=8765 img",
    "docker run --rm \\\n  -p 8765:8765 \\\n  img", "docker compose run -p 8765:8765 coach", "ports:\n  - 8765:8765", 'ports:\n  - "0.0.0.0:8765:8765"',
])
def test_dc008_fires_on_a_published_port_without_the_loopback(bad):
    assert [f.rule for f in L.lint_port_examples(bad)] and {f.rule for f in L.lint_port_examples(bad)} == {"DC008"}


@pytest.mark.parametrize("good", [
    "docker compose run --rm -p 127.0.0.1:8443:8443 coach connect --bank X --country FR", "docker run -p 127.0.0.1:8765:8765 img", "docker run --rm \\\n  -p 127.0.0.1:8765:8765 \\\n  img", "docker run -p [::1]:8765:8765 img",
    'ports:\n  - "127.0.0.1:8765:8765"', "mkdir -p secrets coach-home/data", "docker build -p . ", "echo -p 8765:8765",
])
def test_dc008_accepts_loopback_and_does_not_mistake_other_p_flags_for_ports(good):
    assert L.lint_port_examples(good) == []


# ---------------------------------------------------------------- every rule fires on a bad input

GOOD = DOCKERFILE


@pytest.mark.parametrize("bad,rule", [
    (GOOD.replace("USER 10001:10001", "USER root"), "DF001"),
    (GOOD.replace("USER 10001:10001\n", ""), "DF001"),
    (GOOD.replace("COPY docker/entrypoint.sh", "ADD https://example.com/x.sh"), "DF002"),
    (GOOD.replace("COPY docker/entrypoint.sh", "ADD docker/entrypoint.sh"), "DF002"),
    (GOOD.replace("FROM node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS web", "FROM node:22-bookworm-slim AS web"), "DF003"),            # a floating tag without a digest or a PIN-DIGEST comment
    (GOOD.replace("FROM node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS web", "FROM node:latest AS web"), "DF003"),
    (GOOD.replace("FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS build", "FROM python AS build"), "DF003"),
    (re.sub(r"HEALTHCHECK .*\n    CMD .*\n", "", GOOD), "DF004"),
    (GOOD.replace("ENV PIP_NO_CACHE_DIR=1", "ENV API_TOKEN=abc123 PIP_NO_CACHE_DIR=1"), "DF005"),
    (GOOD.replace("--no-install-recommends ", ""), "DF006"),
    (GOOD.replace("RUN chmod 0755", "RUN chmod 777"), "DF006"),
    (GOOD.replace("RUN corepack enable", "RUN curl -fsSL https://x.example/i.sh | sh"), "DF006"),
    (GOOD.replace('CMD ["ui", "--no-browser"]', "CMD coach ui"), "DF007"),
    (GOOD.replace("WORKDIR /build/web", "WORKDIR build/web"), "DF009"),
])
def test_dockerfile_rules_fire(bad, rule):
    assert bad != GOOD and rule in rules(L.lint_dockerfile(bad))


def test_a_single_stage_dockerfile_and_a_node_runtime_are_refused():
    one = "# PIN-DIGEST: x\nFROM python:3.13-slim\nUSER 1000\nHEALTHCHECK CMD [\"true\"]\nCMD [\"x\"]\n"
    assert "DF008" in rules(L.lint_dockerfile(one))
    node_last = GOOD + "\n# PIN-DIGEST: x\nFROM node:22-slim AS extra\nUSER 1000\nHEALTHCHECK CMD [\"true\"]\n"
    assert "DF008" in rules(L.lint_dockerfile(node_last))


def mutate(fn):
    doc = yaml.safe_load(COMPOSE)
    fn(doc)
    return yaml.safe_dump(doc)


@pytest.mark.parametrize("fn,rule", [
    (lambda d: d["services"]["coach"].__setitem__("ports", ["8765:8765"]), "DC001"),
    (lambda d: d["services"]["coach"].__setitem__("ports", ["0.0.0.0:8765:8765"]), "DC001"),
    (lambda d: d["services"]["coach"].__setitem__("ports", ["8765"]), "DC001"),
    (lambda d: d["services"]["coach"].__setitem__("privileged", True), "DC002"),
    (lambda d: d["services"]["coach"].__setitem__("network_mode", "host"), "DC002"),
    (lambda d: d["services"]["coach"]["volumes"].append("/var/run/docker.sock:/var/run/docker.sock"), "DC002"),
    (lambda d: d["services"]["coach"].__setitem__("user", "0:0"), "DC003"),
    (lambda d: d["services"]["coach"].__setitem__("user", "root"), "DC003"),
    (lambda d: d["services"]["coach"].__setitem__("read_only", False), "DC004"),
    (lambda d: d["services"]["coach"].pop("cap_drop"), "DC004"),
    (lambda d: d["services"]["coach"].pop("security_opt"), "DC004"),
    (lambda d: d["services"]["coach"].__setitem__("environment", {"COACH_DB_KEY": "hunter2"}), "DC005"),
    (lambda d: d["services"]["coach"]["secrets"].append("undeclared"), "DC006"),
    (lambda d: d["services"]["coach"]["volumes"].pop(), "DC007"),
])
def test_compose_rules_fire(fn, rule):
    assert rule in rules(L.lint_compose(mutate(fn)))


def test_compose_user_with_a_default_expression_is_judged_on_the_default():
    assert L.lint_compose(COMPOSE) == []
    assert "DC003" in rules(L.lint_compose(COMPOSE.replace("${COACH_UID:-10001}:${COACH_GID:-10001}", "${COACH_UID:-0}:${COACH_GID:-0}")))


def test_dockerignore_rule_fires():
    assert "DI001" in rules(L.lint_dockerignore("node_modules\n"))
    assert L.lint_dockerignore(IGNORE) == []
    assert "DI001" in rules(L.lint_all(None, None, None))


def test_the_healthcheck_goes_healthy_quickly_after_start():
    line = next(a for _, h, a, _ in L._instructions(DOCKERFILE) if h == "HEALTHCHECK")
    interval = int(re.search(r"--interval=(\d+)s", line).group(1))
    start_interval = int(re.search(r"--start-interval=(\d+)s", line).group(1))
    start_period = int(re.search(r"--start-period=(\d+)s", line).group(1))
    assert interval <= 15 and start_interval <= 5 and 10 <= start_period <= 30      # first healthy report within seconds, not a full interval


def test_the_docs_publish_the_bank_redirect_port_on_the_loopback_only():
    docs = (ROOT / "docs" / "docker.md").read_text()
    assert "-p 127.0.0.1:8443:8443" in docs and "container_bind = true" in docs
    assert L.lint_port_examples(docs, "docs/docker.md") == []
    assert "8443" not in COMPOSE.replace("# ", "") or "127.0.0.1:8443" in COMPOSE                     # never published by `up`
    assert "8443" not in "".join(str(v) for v in yaml.safe_load(COMPOSE)["services"]["coach"].get("ports", []))
