#!/bin/sh
# Entrypoint of the container (E13-1). First start: create the home (configuration from the commented example, memory skeleton, data
# folder, empty encrypted database) in the mounted volumes. Every start: hand over to the coach CLI: `ui --no-browser` by default,
# `schedule loop` for the scheduler, or any other command (`docker compose run --rm coach doctor`).
set -eu

if [ ! -f "${COACH_CONFIG}" ]; then
    echo "coach: first start, creating the home in $(dirname "${COACH_CONFIG}")" >&2
    # COACH_CONFIG points at the file init is about to create: every command loads it first, so init must start without it
    env -u COACH_CONFIG coach init --home "$(dirname "${COACH_CONFIG}")" --data-dir /data --memory-dir /memory --non-interactive >&2
fi

exec coach "$@"
