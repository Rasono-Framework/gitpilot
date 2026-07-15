#!/usr/bin/env bash
# Entrypoint for the FastAPI service container.
#
# Responsibilities:
#   1. Sanity-check that the required env vars are present.
#   2. Tighten .env file permissions if the user mounted it loosely.
#   3. Apply Alembic migrations when SQL state is enabled.
#   4. Start the FastAPI service.
#
# Why we don't `source` the .env file: bash parsing can't represent a
# multi-line PEM value. The Python config loader has its own multi-line
# parser, so we let it read /app/.env directly when env vars are missing.

set -Eeuo pipefail

log() { printf '[entrypoint] %s\n' "$*" >&2; }

require_var() {
    local name="$1"
    if [[ -z "${!name:-}" ]]; then
        log "missing required env var: $name"
        log "hint: pass variables individually with -e NAME=value (PEM-safe)"
        log "      or mount your .env file with -v \$PWD/.env:/app/.env:ro"
        exit 64  # EX_USAGE
    fi
}

# --- 1. Sanity checks -----------------------------------------------------
# If there's a mounted .env that the Python loader can read, defer to it.
# Otherwise, we require the vars to be in the environment.
if [[ ! -f /app/.env ]]; then
    require_var GITHUB_APP_ID
    require_var GITHUB_PRIVATE_KEY
    require_var GITHUB_INSTALLATION_ID
    require_var API_AUTH_TOKEN
else
    log "env vars not set; will load /app/.env via the Python config loader"
fi

# --- 2. Tighten permissions on the mounted .env ---------------------------
if [[ -f /app/.env ]]; then
    mode="$(stat -c '%a' /app/.env 2>/dev/null || stat -f '%Lp' /app/.env)"
    case "$mode" in
        600|400) ;;
        *) log "warning: /app/.env has mode $mode; consider chmod 600" ;;
    esac
fi

# --- 3. Run DB initialization if we are serving ----------------------------
if [[ "${1:-serve}" == "serve" ]]; then
    python -m src.server init-db
fi

# --- 4. Run the server ------------------------------------------------------
# exec so signals (SIGTERM/SIGINT) reach the Python process.
exec python -m src.server "$@"
