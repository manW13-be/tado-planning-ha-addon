#!/bin/bash
# =============================================================================
# cfg/run.sh — Tado Planning Configurator entry point (universal)
#
# Contexts detected automatically:
#   - Inside Docker container (HA add-on)     → /.dockerenv present
#   - macOS (Darwin)                           → uname = Darwin
#
# Usage (Mac):
#   ./cfg/run.sh                    # starts Flask, opens browser
#   ./cfg/run.sh --port 8080        # custom port
#   ./cfg/run.sh --no-browser       # no auto-open
#
# Usage (Docker / HA add-on):
#   CMD ["/run.sh"]  in Dockerfile  → auto-detected, no browser, port 8099
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Context detection
# ---------------------------------------------------------------------------
if [ -f "/.dockerenv" ]; then
    CONTEXT="docker"
elif [ "$(uname)" = "Darwin" ]; then
    CONTEXT="mac"
else
    CONTEXT="linux"
fi

# ---------------------------------------------------------------------------
# Paths and settings per context
# ---------------------------------------------------------------------------
case "$CONTEXT" in
    docker)
        SCHEDULES_DIR="/config/tado-planning/schedules"
        TOKEN_FILE="/data/tado_refresh_token"
        PYTHON="python3"
        SCRIPT="/tado-planning-cfg.py"
        PORT=8099
        HOST="0.0.0.0"
        NO_BROWSER="--no-browser"
        PLANNING_ADDON_SLUG="${PLANNING_ADDON_SLUG:-fc4e2b3e_tado_planning}"
        INGRESS_PATH="${INGRESS_PATH:-}"
        ;;
    mac)
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
        SCHEDULES_DIR="$PROJECT_DIR/schedules"
        TOKEN_FILE="$PROJECT_DIR/tado_refresh_token"
        PYTHON=$(which python3.11 2>/dev/null || which python3)
        SCRIPT="$SCRIPT_DIR/tado-planning-cfg.py"
        PORT="${CFG_PORT:-8080}"
        HOST="127.0.0.1"
        NO_BROWSER=""
        PLANNING_ADDON_SLUG=""
        INGRESS_PATH=""
        ;;
    linux)
        echo "[CFG] ERROR: direct Linux/SSH execution not supported for the configurator."
        echo "[CFG] Access the GUI via the HA sidebar (ingress) once the add-on is running."
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Parse extra args (--port, --no-browser)
# ---------------------------------------------------------------------------
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)    PORT="$2"; shift 2 ;;
        --port=*)  PORT="${1#--port=}"; shift ;;
        --no-browser) NO_BROWSER="--no-browser"; shift ;;
        *) EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
if [ "$CONTEXT" = "docker" ]; then
    VERSION=$(jq -r '.version' /cfg/config.json 2>/dev/null \
              || jq -r '.version' /config/tado-planning-cfg/config.json 2>/dev/null \
              || echo "unknown")
else
    VERSION=$(jq -r '.version' "$SCRIPT_DIR/config.json" 2>/dev/null || echo "unknown")
fi

# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
echo "[CFG] $(date '+%d/%m/%Y %H:%M:%S') — Tado Planning Configurator v${VERSION} (${CONTEXT})"
echo "[CFG] $(date '+%d/%m/%Y %H:%M:%S') — Schedules : ${SCHEDULES_DIR}"
echo "[CFG] $(date '+%d/%m/%Y %H:%M:%S') — Token     : ${TOKEN_FILE}"
echo "[CFG] $(date '+%d/%m/%Y %H:%M:%S') — Listening : ${HOST}:${PORT}"
[ -n "$INGRESS_PATH" ] && echo "[CFG] $(date '+%d/%m/%Y %H:%M:%S') — Ingress   : ${INGRESS_PATH}"

mkdir -p "${SCHEDULES_DIR}"

TADO_SCHEDULES_DIR="${SCHEDULES_DIR}" \
TADO_TOKEN_FILE="${TOKEN_FILE}" \
INGRESS_PATH="${INGRESS_PATH}" \
PLANNING_ADDON_SLUG="${PLANNING_ADDON_SLUG}" \
$PYTHON "$SCRIPT" \
    --host "$HOST" \
    --port "$PORT" \
    ${NO_BROWSER} \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
