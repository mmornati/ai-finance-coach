# syntax=docker/dockerfile:1
#
# AI finance coach: the web app and the CLI in one small image (E13-1). See docs/docker.md.
#
#   stage "web"     builds the React app with Node (pnpm, frozen lockfile)
#   stage "build"   builds the Python wheel (the web build is packaged into it) and installs it, with the LOCKED dependencies, into /opt/venv
#   stage "claude-cli" (opt-in, --build-arg WITH_CLAUDE_CODE=1) fetches the pinned, self-contained Claude Code CLI for the claude-code
#                   backend and checks it against the integrity hash below; without the argument it produces an empty folder
#   stage "runtime" python slim + the venv, non-root, nothing writable but the volumes (/data /memory /config) and a tmpfs /tmp
#
# The base images are pinned by digest (multi-arch index digests, 2026-10-06). To refresh one:
#   docker buildx imagetools inspect <tag>      (the digest is printed as "Digest:")
# `coach dev release-check` refuses a floating tag without a digest.

FROM node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS web
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

# The Claude Code CLI (claude-code backend, docs/docker.md "Claude Code in the container"). One native binary, no Node at runtime. To move to a new
# version set the three values (npm view @anthropic-ai/claude-code-linux-x64@<version> dist.integrity, and the same for -linux-arm64).
FROM node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS claude-cli
ARG WITH_CLAUDE_CODE=0
ARG CLAUDE_CODE_VERSION=2.1.285
ARG CLAUDE_CODE_INTEGRITY_X64=sha512-6qNST8qKemr+rD9zvUEwEq0UtWt0E5Js8bkbjh13/LM62FiGWcN6IqiJPxybJ2FNd+I3liXq6JYNv/vrGnmAow==
ARG CLAUDE_CODE_INTEGRITY_ARM64=sha512-vAvIr+MVnm5IrSq8KoJrZkoG3BFpLvVEqfrx15w7hWW+gcSYHPvN33lfkzXvs5L8MYXxNW0Ae86MLiCH+x4ldQ==
ARG TARGETARCH
WORKDIR /pkg
RUN mkdir -p /out/bin \
    && if [ "$WITH_CLAUDE_CODE" = "1" ]; then \
         case "$TARGETARCH" in \
           amd64) arch=x64; want="$CLAUDE_CODE_INTEGRITY_X64" ;; \
           arm64) arch=arm64; want="$CLAUDE_CODE_INTEGRITY_ARM64" ;; \
           *) echo "no Claude Code build for $TARGETARCH" >&2; exit 1 ;; \
         esac; \
         npm pack --silent "@anthropic-ai/claude-code-linux-${arch}@${CLAUDE_CODE_VERSION}" > /dev/null; \
         tgz="$(ls ./*.tgz)"; \
         got="sha512-$(node -e 'process.stdout.write(require("crypto").createHash("sha512").update(require("fs").readFileSync(process.argv[1])).digest("base64"))' "$tgz")"; \
         [ "$got" = "$want" ] || { echo "Claude Code package: integrity mismatch" >&2; exit 1; }; \
         tar xzf "$tgz" && install -m 0755 package/claude /out/bin/claude; \
       fi

FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 coach \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin coach \
    && mkdir -p /data /memory /config \
    && chown 10001:10001 /data /memory /config
LABEL org.opencontainers.image.source="https://github.com/mmornati/ai-finance-coach" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.description="AI finance coach: a private, self-hosted coach for a household's money"
COPY --from=build /opt/venv /opt/venv
# empty unless built with WITH_CLAUDE_CODE=1; on a Linux host the host's own native `claude` binary can be bind-mounted here instead
COPY --from=claude-cli /out /opt/claude
COPY docker/entrypoint.sh /usr/local/bin/coach-entrypoint
RUN chmod 0755 /usr/local/bin/coach-entrypoint
# No Keychain in a container: secrets are read from files (docker secrets mounted in /run/secrets, mode 0600). The claude-code backend needs the CLI in
# /opt/claude/bin (see above) and the secret claude_code_oauth_token (`claude setup-token`); it never updates itself or sends non-essential traffic.
ENV PATH=/opt/venv/bin:/opt/claude/bin:/usr/local/bin:/usr/bin:/bin \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
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
