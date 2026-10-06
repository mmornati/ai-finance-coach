# syntax=docker/dockerfile:1
#
# AI finance coach: the web app and the CLI in one small image (E13-1). See docs/docker.md.
#
#   stage "web"     builds the React app with Node (pnpm, frozen lockfile)
#   stage "build"   builds the Python wheel (the web build is packaged into it) and installs it, with the LOCKED dependencies, into /opt/venv
#   stage "runtime" python slim + the venv, non-root, nothing writable but the volumes (/data /memory /config) and a tmpfs /tmp
#
# The base images are pinned by digest (multi-arch index digests, 2026-10-06). To refresh one:
#   docker buildx imagetools inspect <tag>      (the digest is printed as "Digest:")
# `coach dev release-check` refuses a floating tag without a digest.

FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS web
WORKDIR /build/web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
# vite.config.ts writes the build to ../src/coach/api/static, that is /build/src/coach/api/static
RUN pnpm build

FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 UV_LINK_MODE=copy
RUN pip install uv==0.12.19
WORKDIR /src
COPY pyproject.toml uv.lock README.md config.example.toml ./
COPY config/import_profiles config/import_profiles
COPY src/ src/
COPY --from=web /build/src/coach/api/static src/coach/api/static
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
    && uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python -r /tmp/requirements.txt
RUN uv build --wheel --out-dir /dist \
    && uv pip install --python /opt/venv/bin/python --no-deps /dist/*.whl

FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 coach \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin coach \
    && mkdir -p /data /memory /config \
    && chown 10001:10001 /data /memory /config
COPY --from=build /opt/venv /opt/venv
COPY docker/entrypoint.sh /usr/local/bin/coach-entrypoint
RUN chmod 0755 /usr/local/bin/coach-entrypoint
# No Keychain in a container: secrets are read from files (docker secrets mounted in /run/secrets, mode 0600), the claude-code backend does not exist here.
ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    COACH_IN_CONTAINER=1 \
    COACH_SECRETS_BACKEND=file \
    COACH_SECRETS_DIR=/run/secrets \
    COACH_CONFIG=/config/config.toml \
    COACH_CONFIG_DIR=/config/user
WORKDIR /config
VOLUME ["/data", "/memory", "/config"]
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8765), 3).close()"]
ENTRYPOINT ["/usr/local/bin/coach-entrypoint"]
CMD ["ui", "--no-browser"]
