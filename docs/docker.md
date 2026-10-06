# Running in Docker (E13-1)

The image runs the web app and the CLI without a Mac: no Keychain (secrets are files), no `claude` command (cloud model through the
Anthropic API, or a local Ollama), and the app reachable on the host's loopback only.

> **Status.** `Dockerfile`, `docker-compose.yml`, `.dockerignore` and `docker/entrypoint.sh` are checked **statically** by the test suite
> (`tests/test_docker_static.py`, `coach/dockerlint.py`): non-root user, no remote `ADD`, pinned (or placeholder-pinned) base images,
> loopback-only port, read-only root filesystem, dropped capabilities, secrets from files. The automated checks never build the image (no Docker daemon, no network there). It was **built and started once by hand** (2026-10-06, OrbStack, Docker 29): first start, a root-owned `/run/secrets`, healthy web app on the host's loopback only, read-only root, non-root user, dropped capabilities, restart without re-init, the `scheduler` profile; that run found and fixed the first-start bug (`init` loaded the config it was about to create). Not exercised: a bank link, the Anthropic backend, Ollama.

## Files and volumes

| Host (`./`) | In the container | What |
|---|---|---|
| `coach-home/data` | `/data` | encrypted database, logs, TLS certificate, web-app state (private) |
| `coach-home/memory` | `/memory` | the household memory (your files; created from templates on first start) |
| `coach-home/config` | `/config` | `config.toml`, your taxonomy / rules copies (`/config/user`), CSV import profiles |
| `secrets/` | `/run/secrets` (read-only) | `db_key`, `backup_key`, `proposal_key` (one file each, the value only, mode 0600) |

`coach-home/` and `secrets/` are git-ignored and Docker-ignored. The root filesystem of the container is read-only; `/tmp` is a tmpfs.

## First start

```bash
mkdir -p secrets coach-home/data coach-home/memory coach-home/config && chmod 700 secrets
for k in db_key backup_key proposal_key; do
  (umask 077; head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' > "secrets/$k")
done
export COACH_UID=$(id -u) COACH_GID=$(id -g)      # the volumes belong to you; the container never runs as root
docker compose build
```

Back up `secrets/` in your password manager: losing `db_key` makes the database unrecoverable, losing `backup_key` the backups unreadable.
(Another way to create them: `coach init --secrets-only --secrets-backend file --secrets-dir ./secrets --non-interactive` with an installed `coach`.)

On the first start the entrypoint runs `coach init --non-interactive`: it writes `/config/config.toml` from the commented example with
**container defaults** (`[llm]` and `[coach]` backend `anthropic-api`, `[ui] host = "0.0.0.0"` inside the container, no browser), copies the
memory skeleton, and creates the empty encrypted database with the `db_key` secret. Existing files are never overwritten.

```bash
docker compose run --rm coach doctor               # what is ready
docker compose run --rm coach setup enablebanking  # your own Enable Banking application (docs: README, quick start step 3)
docker compose run --rm coach setup                # the first-run wizard
docker compose up -d                               # web app: http://127.0.0.1:8765
cat coach-home/data/login-link.txt                  # the one-time login link (not in `docker logs`); a fresh one: docker compose exec coach coach ui --login-link
```

The Enable Banking key: `coach setup enablebanking` asks for the path of the downloaded `.pem` *inside the container*. Mount it for that one command
(`docker compose run --rm -v "$PWD/eb.pem:/tmp/eb.pem:ro" coach setup enablebanking`) and give `/tmp/eb.pem`; the guide keeps a private copy in
`/config/enablebanking/`. Delete the download afterwards.

## Connecting a bank from a container

The redirect URL registered in Enable Banking stays `https://localhost:8443/callback`. With the port published on your host the browser reaches the container at
that exact address, so nothing changes in the control panel.

```bash
docker compose run --rm -p 127.0.0.1:8443:8443 coach connect --bank "<bank name>" --country FR
```

* The image's `coach init` writes `[callback] container_bind = true`; with that setting **and** a real container (marker file or cgroup, not an environment
  variable) the HTTPS redirect server listens on `0.0.0.0` inside the container, with the self-signed certificate in `/data/tls` (your browser warns once: accept it).
  Anywhere else it listens on loopback only, and the setting on a host is refused. A configuration created before this setting existed needs `container_bind = true`
  in its `[callback]` section (or recreate it).
* **Publish the port on `127.0.0.1` only** (`-p 127.0.0.1:8443:8443`). The command prints a loud warning; `coach dev release-check` (rule DC008) fails when the compose
  file, the README or these docs show a published port, 8443 included, without `127.0.0.1`. There is deliberately no service that publishes it on `up`.
* The URL to open is printed in the terminal (there is no browser inside the container): open it on your host, log in at the bank, accept the certificate warning. The
  server validates the `state`, serves **one** valid request, completes the session and stops; neither the code nor the state is written to any log.
* The first-run wizard in a container shows this command (to run in another terminal on the host) and keeps copy-paste as the fallback.

**Fallback, copy-paste** (`--no-server`): open the printed URL, log in, then copy the **whole address** the browser ends on (the page fails to load, that is
expected) **exactly as shown** and run it **in single quotes, without backslashes**:

```bash
docker compose run --rm coach connect --bank "<bank name>" --country FR --no-server
docker compose run --rm coach finish '<the address from the browser>'
```

`coach finish` strips a shell-escaped paste (backslashes before `?`, `=`, `&`), surrounding quotes and line wraps, and says "the address has no ?code=... part" without
echoing it. The code is single-use and short-lived: if the bank rejects it, run `connect` again. Do not run the host's `uv run coach finish`: it would act on the host's
home, not the container's.

## Secrets and model backends

* **Secrets**: files in `/run/secrets` through Docker secrets (compose file-based secrets are bind mounts: the file's mode is the host's, which
  is why `COACH_UID` / `COACH_GID` matter). `COACH_SECRETS_BACKEND=file` and `COACH_SECRETS_DIR=/run/secrets` are set in the image. An environment
  variable (`COACH_DB_KEY`...) wins over a file, but keep keys out of the compose file. What the backend checks: the folder `/run/secrets` may be **root-owned, mode 0755** (that is how Docker mounts it for a non-root
  container user) or yours; a folder owned by anyone else, or writable by group / others (a sticky tmpfs aside), is refused. Each secret must be a regular
  file owned by you or by root, not writable by group / others, and not readable by group / others **unless** you opt in: with plain `docker compose` the
  file is a bind mount of your host file, so keep it `chmod 600` and run the container as the same user (`COACH_UID`). With swarm or Kubernetes, which mount
  secrets root-owned, either give each secret `mode: 0400` and the container user's `uid` in the compose long syntax
  (`secrets: [{source: db_key, uid: "10001", mode: 0400}]`; plain compose ignores `uid` / `mode` for file secrets) or set `COACH_SECRETS_ALLOW_READABLE=1`
  (group / other *readable* accepted, *writable* never).
  Verified by hand on Docker 29 / OrbStack: a root-owned 0755 `/run/secrets` with host files `chmod 600` run as the same uid is accepted.
* **Anthropic API**: add a fourth file `secrets/anthropic_api_key` and list it under `secrets:` of the service (and at the top level), or pass
  `ANTHROPIC_API_KEY` from your shell or an `.env` file (git-ignored). Then the web app's coach and `classify` work.
* **Ollama** (a model on your own machine): set `[llm] backend` and `[coach] backend` to `"ollama"` and `[llm] ollama_url` to a server reachable
  from the container (`http://host.docker.internal:11434` for Ollama on the Docker host). That address is not the container's loopback, so it
  needs `[llm] ollama_allow_remote = true`, and `[privacy] local_only = true` **refuses** it (local-only means a loopback server). To keep the
  strict mode, run `coach` and Ollama in the same network namespace yourself; the shipped compose file does not. `coach doctor` flags a
  `claude-code` backend as a failure in a container.

## The daily job

```bash
docker compose --profile scheduler up -d            # adds the `scheduler` service: `coach schedule loop`
```

The loop runs the same job as launchd (sync within the PSD2 limits, normalize, classify with the backend you chose, analytics, local alerts,
weekly checks) once a day at `[schedule] time` (container time, usually UTC: set `TZ` if you want local time). It is opt-in because it contacts Enable
Banking and your model backend every day. A restart does not run the missed time (`--run-now` does). A failing run is logged and the loop continues.

## Security posture of the container

Non-root numeric user (`10001` by default, or yours), `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, `init: true`, no `privileged`, no host network, no Docker
socket, the port published as `127.0.0.1:8765:8765` only.

* **The bind.** Inside the container the app listens on all interfaces because that is how a published port reaches it. `coach/api/server.py` accepts that **only when
  both hold**: the process really runs in a container (`/.dockerenv`, `/run/.containerenv` or a container runtime in PID 1's cgroup: not an environment variable) **and**
  the configuration says `[ui] container_bind = true`, which only the image's `coach init` writes. Otherwise it refuses, and it prints a loud warning at start that the port must
  be published on the host's `127.0.0.1` only. A configuration created before this setting existed must be recreated or edited by you.
* **Publishing.** With `docker run` always write the loopback address, never a bare port:

  ```bash
  docker run --rm -it --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
    --user "$(id -u):$(id -g)" -p 127.0.0.1:8765:8765 \
    -v "$PWD/coach-home/data:/data" -v "$PWD/coach-home/memory:/memory" -v "$PWD/coach-home/config:/config" \
    -v "$PWD/secrets:/run/secrets:ro" ai-finance-coach:local ui --no-browser
  ```

  `-p 8765:8765` or `-p 0.0.0.0:8765:8765` would publish your bank data on every interface of the host. `coach dev release-check` (rule DC008) fails when the compose file, the
  README or these docs show a published port without `127.0.0.1`, and `tests/test_docker_static.py` keeps the compose file that way.
* **The login link.** Access to `docker logs` (anyone in the `docker` group, or who can reach the Docker socket) **is** access to the app, if the one-time link were printed there.
  In a container without a terminal the link is therefore **not** printed: it is written to `login-link.txt` in the data volume (mode 0600, replaced at each start), and
  `docker compose exec coach coach ui --login-link` prints a fresh one to your terminal. Treat `/var/run/docker.sock` and the `docker` group as root on the host.
* For access from other devices use Tailscale in front of the loopback port (docs/ui.md), never a public port.

## Pinning the base images

The three `FROM` lines carry a `PIN-DIGEST` comment: before you publish an image, replace each tag by `tag@sha256:<digest>`
(`docker buildx imagetools inspect python:3.13-slim-bookworm`). `coach dev release-check` accepts a digest or the comment, and refuses a floating
tag without one.

## Known gaps

* Not built or run by the project's checks; the `sqlcipher3-wheels` Linux wheels for the chosen Python are assumed, not verified here.
* The wizard's Enable Banking and bank steps work in a container through the copy-paste flow described above, which is less smooth than on a laptop.
* No image is published; build it yourself.
