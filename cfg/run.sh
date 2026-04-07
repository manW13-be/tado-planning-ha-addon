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
if [ -f "/tado-planning-cfg.py" ]; then
    # Script is baked at root by Dockerfile — we're inside the addon container
    CONTEXT="docker"
elif [ "$(uname)" = "Darwin" ]; then
    CONTEXT="mac"
else
    # HA SSH or any other Linux shell
    CONTEXT="linux"
fi

# ---------------------------------------------------------------------------
# Paths and settings per context
# ---------------------------------------------------------------------------
case "$CONTEXT" in
    docker)
        SCHEDULES_DIR="/config/tado-planning/schedules"
        TOKEN_FILE="/config/tado-planning/tado_refresh_token"
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
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
        SCHEDULES_DIR="${TADO_SCHEDULES_DIR:-$PROJECT_DIR/schedules}"
        TOKEN_FILE="${TADO_TOKEN_FILE:-$PROJECT_DIR/tado_refresh_token}"
        SCRIPT="$SCRIPT_DIR/tado-planning-cfg.py"
        PORT="${CFG_PORT:-8099}"
        HOST="0.0.0.0"
        NO_BROWSER="--no-browser"
        PLANNING_ADDON_SLUG=""
        INGRESS_PATH=""

        # Persistent venv in /config/ — survives HA reboots
        VENV_DIR="/config/tado-planning-cfg/venv"
        if [ ! -f "$VENV_DIR/bin/python3" ]; then
            echo "[CFG] Creating Python venv at $VENV_DIR..."
            mkdir -p "$(dirname "$VENV_DIR")"
            python3 -m venv "$VENV_DIR"
        fi
        PYTHON="$VENV_DIR/bin/python3"

        # Check and install only missing packages
        MISSING=()
        "$PYTHON" -c "import flask"       2>/dev/null || MISSING+=("flask")
        "$PYTHON" -c "import requests"    2>/dev/null || MISSING+=("requests")
        "$PYTHON" -c "import PyTado"      2>/dev/null || MISSING+=("python-tado>=0.18")
        if [ ${#MISSING[@]} -gt 0 ]; then
            echo "[CFG] Installing missing packages: ${MISSING[*]}"
            "$VENV_DIR/bin/pip" install --quiet "${MISSING[@]}"
            echo "[CFG] Done."
        fi
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
# Resolve access URL for display in logs
# ---------------------------------------------------------------------------
get_access_url() {
    if [ "$CONTEXT" = "docker" ] || [ "$CONTEXT" = "linux" ]; then
        # Try HA hostname from supervisor API
        local HA_HOST=""
        if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
            HA_HOST=$(curl -sf \
                -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
                http://supervisor/core/api/config 2>/dev/null \
                | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('external_url') or d.get('internal_url',''))" \
                2>/dev/null | sed 's|https\?://||' | cut -d'/' -f1 || true)
        fi
        # Fallback: system hostname
        if [ -z "$HA_HOST" ]; then
            HA_HOST=$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "ha2.local")
        fi
        echo "http://${HA_HOST}:${PORT}"
    else
        echo "http://localhost:${PORT}"
    fi
}

ACCESS_URL=$(get_access_url)

# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
echo "[CFG] $(date '+%d/%m/%Y %H:%M:%S') — Tado Planning Configurator v${VERSION} (${CONTEXT})"
echo "[CFG] $(date '+%d/%m/%Y %H:%M:%S') — Schedules : ${SCHEDULES_DIR}"
echo "[CFG] $(date '+%d/%m/%Y %H:%M:%S') — Token     : ${TOKEN_FILE}"
echo "[CFG] $(date '+%d/%m/%Y %H:%M:%S') — Listening : ${HOST}:${PORT}"
echo "[CFG] $(date '+%d/%m/%Y %H:%M:%S') — Access    : ${ACCESS_URL}"
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
