"""A small static linter for the Dockerfile and docker-compose.yml of the project (E13-1).

No docker daemon, no network, no hadolint: the rules that matter for a tool that holds a household's bank data are checked on the text.
Used by ``tests/test_docker_static.py`` and by ``coach dev release-check``.

Dockerfile (hadolint-like)
    DF001  the last stage runs as a non-root USER (named or numeric, not root / 0), set before CMD / ENTRYPOINT
    DF002  no ADD (use COPY); an ADD of a remote URL is always an error
    DF003  every FROM is pinned: a digest (``@sha256:...``) or an explicit ``PIN-DIGEST`` placeholder comment right above it, never ``latest`` / untagged
    DF004  a HEALTHCHECK exists
    DF005  no secret-looking ENV / ARG with a literal value
    DF006  apt-get install uses --no-install-recommends and the lists are removed; no `curl | sh`; no sudo; no chmod 777
    DF007  CMD / ENTRYPOINT in exec (JSON) form
    DF008  multi-stage: at least two stages, and the last one is not the Node build stage
    DF009  absolute WORKDIR
docker-compose.yml
    DC001  every published port is bound to 127.0.0.1 (or ::1) explicitly
    DC002  no privileged mode, no host network, no docker socket
    DC003  no service runs as root (an explicit ``user:`` is numeric or named and not root / 0)
    DC004  read_only root filesystem, all capabilities dropped, no-new-privileges
    DC005  no literal secret in ``environment`` (use ``secrets:`` or ``env_file``)
    DC006  the secrets come from top-level ``secrets:`` and the services mount them
    DC007  the volumes for data, memory and config are bind mounts or named volumes (not the image's own layer)
    DC008  any `docker run -p` / `--publish` / `ports:` example in the compose file or the docs publishes on 127.0.0.1 (or ::1) only
.dockerignore
    DI001  the data, memory, secrets and key files are kept out of the build context
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import yaml

SECRET_NAME = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)", re.I)
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")
PLACEHOLDER = re.compile(r"PIN-DIGEST", re.I)
NOT_A_SECRET_SUFFIX = ("_FILE", "_DIR", "_PATH", "_BACKEND")      # COACH_SECRETS_DIR names a folder, not a value
ROOT_USERS = {"root", "0", "0:0", "root:root", "0:root"}


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str
    line: int = 0

    def __str__(self) -> str:
        return f"{self.rule}{f' (line {self.line})' if self.line else ''}: {self.message}"


def _instructions(text: str) -> list[tuple[int, str, str, list[str]]]:
    """(line, INSTRUCTION, arguments, comment lines directly above) with backslash continuations joined."""
    out = []
    lines = text.splitlines()
    i, comments = 0, []
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        if not s:
            comments = []
            i += 1
            continue
        if s.startswith("#"):
            comments.append(s)
            i += 1
            continue
        start = i + 1
        buf = s
        while buf.endswith("\\") and i + 1 < len(lines):
            i += 1
            nxt = lines[i].strip()
            if nxt.startswith("#"):
                continue
            buf = buf[:-1].rstrip() + " " + nxt
        i += 1
        head, _, rest = buf.partition(" ")
        out.append((start, head.upper(), rest.strip(), comments))
        comments = []
    return out


def lint_dockerfile(text: str) -> list[Finding]:
    ins = _instructions(text)
    f: list[Finding] = []
    froms = [(n, a, c) for n, h, a, c in ins if h == "FROM"]
    stages = []
    for n, args, comments in froms:
        parts = args.split()
        parts = [p for p in parts if not p.startswith("--")]
        image = parts[0] if parts else ""
        alias = parts[2] if len(parts) >= 3 and parts[1].upper() == "AS" else None
        stages.append((image, alias, n))
        if image in {s[1] for s in stages[:-1] if s[1]}:          # FROM an earlier stage
            continue
        if image.lower() == "scratch":
            continue
        has_digest = bool(DIGEST.search(image))
        placeholder = any(PLACEHOLDER.search(c) for c in comments)
        tag = image.split("@")[0].rpartition(":")[2] if ":" in image.split("@")[0].rpartition("/")[2] else ""
        if not has_digest and not placeholder:
            f.append(Finding("DF003", f"{image} is not pinned: use image@sha256:<digest> or a '# PIN-DIGEST: ...' comment above the FROM", n))
        if not tag and not has_digest:
            f.append(Finding("DF003", f"{image} has no tag (untagged means latest)", n))
        if tag == "latest":
            f.append(Finding("DF003", f"{image} uses the latest tag", n))
    if len(stages) < 2:
        f.append(Finding("DF008", "use a multi-stage build (the web build must not ship in the runtime image)", 0))
    elif stages[-1][0].lower().startswith("node"):
        f.append(Finding("DF008", "the last stage is a Node image: the runtime must be the Python stage", stages[-1][2]))

    last_from = froms[-1][0] if froms else 0
    final = [(n, h, a) for n, h, a, _ in ins if n >= last_from]
    users = [(n, a) for n, h, a in final if h == "USER"]
    if not users:
        f.append(Finding("DF001", "no USER in the last stage: the container would run as root", 0))
    else:
        n, u = users[-1]
        if u.strip().lower() in ROOT_USERS or u.split(":")[0].strip().lower() in ("root", "0"):
            f.append(Finding("DF001", "the last USER is root", n))
        starts = [n2 for n2, h, _ in final if h in ("CMD", "ENTRYPOINT")]
        if starts and min(starts) < n:
            f.append(Finding("DF001", "USER must come before CMD / ENTRYPOINT", n))
    if not any(h == "HEALTHCHECK" for n, h, a in final):
        f.append(Finding("DF004", "no HEALTHCHECK in the last stage", 0))

    for n, h, a, _ in ins:
        if h == "ADD":
            remote = re.search(r"(https?|ftp)://|git@|git://", a)
            f.append(Finding("DF002", "ADD of a remote URL" if remote else "use COPY instead of ADD", n))
        if h in ("ENV", "ARG"):
            for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)=(\S+)", a) if "=" in a else []:
                if (SECRET_NAME.search(m.group(1)) and not m.group(1).endswith(NOT_A_SECRET_SUFFIX)
                        and not m.group(2).startswith(("$", '"$')) and m.group(2) not in ('""', "''")):
                    f.append(Finding("DF005", f"{m.group(1)} looks like a secret with a literal value", n))
        if h == "RUN":
            if "apt-get install" in a:
                if "--no-install-recommends" not in a:
                    f.append(Finding("DF006", "apt-get install without --no-install-recommends", n))
                if "/var/lib/apt/lists" not in a:
                    f.append(Finding("DF006", "apt lists are not removed in the same RUN", n))
            if re.search(r"(curl|wget)[^|;&]*\|\s*(sudo\s+)?(sh|bash)\b", a):
                f.append(Finding("DF006", "piping a download into a shell", n))
            if re.search(r"\bsudo\b", a):
                f.append(Finding("DF006", "sudo in a Dockerfile", n))
            if re.search(r"chmod\s+(-R\s+)?(0?777|a\+rwx)", a):
                f.append(Finding("DF006", "chmod 777", n))
        if h in ("CMD", "ENTRYPOINT") and not a.startswith("["):
            f.append(Finding("DF007", f"{h} should use the exec (JSON array) form", n))
        if h == "WORKDIR" and not a.startswith(("/", "$")):
            f.append(Finding("DF009", "WORKDIR must be an absolute path", n))
    return f


def _ports(svc: dict) -> list[str]:
    out = []
    for p in svc.get("ports") or []:
        out.append(p if isinstance(p, str) else str(p.get("host_ip", "")) + "|" + str(p.get("published", "")))
    return out


def lint_compose(text: str) -> list[Finding]:
    f: list[Finding] = []
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        return [Finding("DC000", f"not valid YAML: {e}")]
    services = doc.get("services") or {}
    top_secrets = doc.get("secrets") or {}
    if not services:
        f.append(Finding("DC000", "no services"))
    for name, svc in services.items():
        for p in svc.get("ports") or []:
            if isinstance(p, dict):
                host_ip = str(p.get("host_ip", ""))
            else:
                parts = str(p).split(":")
                host_ip = parts[0] if len(parts) == 3 else ""
                if len(parts) > 3:                           # [::1]:8765:8765
                    host_ip = ":".join(parts[:-2])
            if host_ip.strip("[]") not in ("127.0.0.1", "::1", "localhost"):
                f.append(Finding("DC001", f"{name}: port {p!r} is not bound to 127.0.0.1 (write '127.0.0.1:HOST:CONTAINER')"))
        if svc.get("privileged"):
            f.append(Finding("DC002", f"{name}: privileged mode"))
        if str(svc.get("network_mode", "")).lower() == "host":
            f.append(Finding("DC002", f"{name}: host network"))
        for v in svc.get("volumes") or []:
            src = v if isinstance(v, str) else str(v.get("source", ""))
            if "docker.sock" in src:
                f.append(Finding("DC002", f"{name}: the docker socket is mounted"))
        user = svc.get("user")
        if user is not None:
            u = str(user)
            base = re.sub(r"\$\{[A-Z_]+:-([^}]*)\}", r"\1", u)
            if base.strip().lower() in ROOT_USERS or base.split(":")[0].lower() in ("root", "0"):
                f.append(Finding("DC003", f"{name}: runs as root ({u})"))
        if svc.get("read_only") is not True:
            f.append(Finding("DC004", f"{name}: read_only: true is missing (the data lives in the volumes and a tmpfs)"))
        caps = [str(c).upper() for c in (svc.get("cap_drop") or [])]
        if "ALL" not in caps:
            f.append(Finding("DC004", f"{name}: cap_drop: [ALL] is missing"))
        if not any("no-new-privileges" in str(o) and "false" not in str(o).lower() for o in (svc.get("security_opt") or [])):
            f.append(Finding("DC004", f"{name}: security_opt no-new-privileges:true is missing"))
        env = svc.get("environment") or {}
        pairs = env.items() if isinstance(env, dict) else [tuple(str(e).split("=", 1)) + ("",) for e in env]
        for item in pairs:
            k, v = item[0], item[1]
            if SECRET_NAME.search(str(k)) and v not in (None, "") and not str(v).startswith("$") and not str(k).endswith(NOT_A_SECRET_SUFFIX):
                f.append(Finding("DC005", f"{name}: {k} holds a literal value (use secrets: or env_file)"))
        for s in svc.get("secrets") or []:
            sname = s if isinstance(s, str) else s.get("source")
            if sname not in top_secrets:
                f.append(Finding("DC006", f"{name}: secret {sname!r} is not declared at the top level"))
        vols = [v if isinstance(v, str) else f"{v.get('source', '')}:{v.get('target', '')}" for v in svc.get("volumes") or []]
        for target in ("/data", "/memory", "/config"):
            if not any(re.search(rf":{re.escape(target)}(:|$)", v) for v in vols):
                f.append(Finding("DC007", f"{name}: no volume mounted at {target}"))
    for sname, s in top_secrets.items():
        if isinstance(s, dict) and "environment" in s and "file" not in s:
            f.append(Finding("DC006", f"secret {sname}: prefer a file source over an environment variable"))
    return f


_RUN_PORT = re.compile(r"(?:^|\s)(?:-p|--publish)(?:\s+|=)(\S+)")
_YAML_PORT = re.compile(r"""^\s*-\s*["']?(\[?[0-9a-fA-F.:]*\]?:?\d+:\d+(?:/\w+)?)["']?\s*$""")
LOOPBACK_PREFIXES = ("127.0.0.1:", "[::1]:", "localhost:")


def lint_port_examples(text: str, name: str = "") -> list[Finding]:
    """DC008: every published port in a document (a `docker run -p`, a `--publish`, a `- "H:C"` line of a ports: list) names the loopback address.
    `-p` is read only inside a `docker ... run` command (a continuation line included): `mkdir -p` is not a port."""
    out = []
    in_run = False
    for n, line in enumerate(text.splitlines(), 1):
        starts = bool(re.search(r"\bdocker(?:\s+compose)?\s+(?:container\s+)?(?:run|create)\b", line))
        values = []
        if starts or in_run:
            values += _RUN_PORT.findall(line)
        elif "--publish" in line:
            values += _RUN_PORT.findall(line)
        in_run = (starts or in_run) and line.rstrip().endswith("\\")
        m = _YAML_PORT.match(line)
        if m:
            values.append(m.group(1))
        for v in values:
            v = v.strip("\"'")
            if not v.startswith(LOOPBACK_PREFIXES):
                out.append(Finding("DC008", f"{name or 'document'}: port {v!r} is published without 127.0.0.1 (write 127.0.0.1:HOST:CONTAINER)", n))
    return out


def lint_dockerignore(text: str) -> list[Finding]:
    lines = {ln.strip().rstrip("/").lstrip("/") for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")}
    f = []
    for must in ("data", "memory", "secrets", "coach-home", "backups", ".env", "config.toml", "*.pem", ".git", ".venv", "web/node_modules"):
        if must not in lines and f"**/{must}" not in lines:
            f.append(Finding("DI001", f".dockerignore does not exclude {must}"))
    return f


def lint_all(dockerfile: Optional[str], compose: Optional[str], dockerignore: Optional[str]) -> list[Finding]:
    out: list[Finding] = []
    out += lint_dockerfile(dockerfile) if dockerfile is not None else [Finding("DF000", "Dockerfile is missing")]
    out += lint_compose(compose) if compose is not None else [Finding("DC000", "docker-compose.yml is missing")]
    out += lint_dockerignore(dockerignore) if dockerignore is not None else [Finding("DI001", ".dockerignore is missing")]
    return out
