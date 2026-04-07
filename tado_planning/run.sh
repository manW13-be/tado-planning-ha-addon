#!/bin/bash
# =============================================================================
# tado_planning/run.sh — Universal entry point
#
# Contexts:
#   docker  → HA addon container (SUPERVISOR_TOKEN absent from FS, script at /)
#   mac     → macOS local dev
#   linux   → HA SSH debug
#
# Docker mode starts both:
#   - tado-planning.py  (hourly loop, background)
#   - tado-planning-cfg (Flask web UI, foreground — keeps container alive)
#
# Mac/Linux mode starts only what's requested:
#   --loop  → planning loop only (no Flask)
#   --cfg   → Flask only
#   (none)  → single planning run
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Context detection
# ---------------------------------------------------------------------------
if [ -f "/tado-planning.py" ]; then
    CONTEXT="docker"
elif [ "$(uname)" = "Darwin" ]; then
    CONTEXT="mac"
else
    CONTEXT="linux"
fi

# ---------------------------------------------------------------------------
# Paths per context
# ---------------------------------------------------------------------------
case "$CONTEXT" in
    docker)
        VERBOSITY=$(jq -r '.verbosity // 0' /data/options.json 2>/dev/null || echo "0")
        SCHEDULES_DIR="/config/tado-planning/schedules"
        SCHEDULES_TMPL="/schedules.tmpl"
        TOKEN_FILE="/config/tado-planning/tado_refresh_token"
        PYTHON="python3"
        PLANNING_SCRIPT="/tado-planning.py"
        CFG_SCRIPT="/tado-planning-cfg.py"
        CFG_PORT=8099
        CFG_HOST="0.0.0.0"
        ;;
    mac)
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
        SCHEDULES_DIR="$PROJECT_DIR/schedules"
        SCHEDULES_TMPL="$PROJECT_DIR/schedules.tmpl"
        TOKEN_FILE="$PROJECT_DIR/tado_refresh_token"
        PYTHON=$(which python3.11 2>/dev/null || which python3)
        PLANNING_SCRIPT="$SCRIPT_DIR/tado-planning.py"
        CFG_SCRIPT="$SCRIPT_DIR/tado-planning-cfg.py"
        CFG_PORT="${CFG_PORT:-8080}"
        CFG_HOST="127.0.0.1"
        VERBOSITY=0
        ;;
    linux)
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
        SCHEDULES_DIR="${TADO_SCHEDULES_DIR:-$PROJECT_DIR/schedules}"
        SCHEDULES_TMPL="$PROJECT_DIR/schedules.tmpl"
        TOKEN_FILE="${TADO_TOKEN_FILE:-$PROJECT_DIR/tado_refresh_token}"
        PLANNING_SCRIPT="$SCRIPT_DIR/tado-planning.py"
        CFG_SCRIPT="$SCRIPT_DIR/tado-planning-cfg.py"
        CFG_PORT="${CFG_PORT:-8099}"
        CFG_HOST="0.0.0.0"
        VERBOSITY=0

        # Persistent venv in /config/ — survives HA reboots
        VENV_DIR="/config/tado-planning/venv"
        if [ ! -f "$VENV_DIR/bin/python3" ]; then
            echo "[TADO] Creating Python venv at $VENV_DIR..."
            mkdir -p "$(dirname "$VENV_DIR")"
            python3 -m venv "$VENV_DIR"
        fi
        PYTHON="$VENV_DIR/bin/python3"

        MISSING=()
        "$PYTHON" -c "import PyTado" 2>/dev/null || MISSING+=("python-tado>=0.18")
        "$PYTHON" -c "import flask"   2>/dev/null || MISSING+=("flask")
        "$PYTHON" -c "import requests" 2>/dev/null || MISSING+=("requests")
        if [ ${#MISSING[@]} -gt 0 ]; then
            echo "[TADO] Installing: ${MISSING[*]}"
            "$VENV_DIR/bin/pip" install --quiet "${MISSING[@]}"
        fi
        ;;
esac

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
LOOP=false
RUN_CFG=false
PYTHON_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --loop) LOOP=true ;;
        --cfg)  RUN_CFG=true ;;
        *)      PYTHON_ARGS+=("$arg") ;;
    esac
done

# Docker --loop → start both
if [ "$LOOP" = true ] && [ "$CONTEXT" = "docker" ]; then
    VFLAG=""
    if [ "${VERBOSITY:-0}" -gt 0 ] 2>/dev/null; then
        VFLAG="-$(printf '%0.sv' $(seq 1 "$VERBOSITY"))"
    fi
    PYTHON_ARGS=($VFLAG)
    RUN_CFG=true
fi

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
if [ "$CONTEXT" = "docker" ]; then
    VERSION=$(jq -r '.version' /config/tado-planning/config.json 2>/dev/null || echo "unknown")
elif [ "$CONTEXT" = "mac" ]; then
    VERSION=$(jq -r '.version' "$SCRIPT_DIR/config.json" 2>/dev/null || echo "unknown")
else
    VERSION=$(jq -r '.version' "$SCRIPT_DIR/config.json" 2>/dev/null || echo "unknown")
fi

# ---------------------------------------------------------------------------
# Init schedules
# ---------------------------------------------------------------------------
init_schedules() {
    if [ ! -d "$SCHEDULES_DIR" ] || [ -z "$(ls -A "$SCHEDULES_DIR" 2>/dev/null)" ]; then
        if [ -d "$SCHEDULES_TMPL" ]; then
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Initializing schedules from schedules.tmpl/..."
            mkdir -p "$SCHEDULES_DIR"
            cp "$SCHEDULES_TMPL"/* "$SCHEDULES_DIR/"
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — ⚠ Review schedule files before next run"
        else
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — ERROR: schedules/ not found and no schedules.tmpl/"
            exit 1
        fi
    else
        echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Schedules: $(ls "$SCHEDULES_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ') file(s)"
    fi
}

next_run_time() {
    NEXT=$(( 3600 - $(date +%s) % 3600 ))
    if [ "$(uname)" = "Darwin" ]; then
        date -v+${NEXT}S '+%d/%m/%Y %H:%M:%S'
    else
        date -d "+${NEXT} seconds" '+%d/%m/%Y %H:%M:%S'
    fi
}

# ---------------------------------------------------------------------------
# Resolve access URL for cfg
# ---------------------------------------------------------------------------
get_cfg_url() {
    local HA_HOST=""
    if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
        HA_HOST=$(curl -sf \
            -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
            http://supervisor/core/api/config 2>/dev/null \
            | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('internal_url',''))" \
            2>/dev/null | sed 's|https\?://||' | cut -d'/' -f1 || true)
    fi
    if [ -z "$HA_HOST" ]; then
        HA_HOST=$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "ha2.local")
    fi
    echo "http://${HA_HOST}:${CFG_PORT}"
}

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if [ "$LOOP" = true ] && [ "$CONTEXT" = "docker" ]; then

    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Add-on started — v$VERSION — verbosity: $VERBOSITY"
    init_schedules

    # Start Flask configurator in background
    CFG_URL=$(get_cfg_url)
    echo "[CFG]  $(date '+%d/%m/%Y %H:%M:%S') — Starting configurator on ${CFG_HOST}:${CFG_PORT}"
    echo "[CFG]  $(date '+%d/%m/%Y %H:%M:%S') — Access : ${CFG_URL}"
    TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
    TADO_TOKEN_FILE="$TOKEN_FILE" \
    PLANNING_ADDON_SLUG="fc4e2b3e_tado_planning" \
    $PYTHON "$CFG_SCRIPT" --host "$CFG_HOST" --port "$CFG_PORT" --no-browser &
    CFG_PID=$!

    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Starting planning loop..."
    while true; do
        echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Running..."
        TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
        TADO_TOKEN_FILE="$TOKEN_FILE" \
        $PYTHON "$PLANNING_SCRIPT" ${PYTHON_ARGS[@]+"${PYTHON_ARGS[@]}"} 2>&1 \
            || echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Script exited with error $?"
        echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Next run at $(next_run_time)"
        sleep $(( 3600 - $(date +%s) % 3600 ))
    done

elif [ "$RUN_CFG" = true ]; then

    # CFG only (Mac --cfg or Linux --cfg)
    init_schedules
    CFG_URL=$(get_cfg_url)
    echo "[CFG]  $(date '+%d/%m/%Y %H:%M:%S') — Configurator v$VERSION (${CONTEXT})"
    echo "[CFG]  $(date '+%d/%m/%Y %H:%M:%S') — Schedules : $SCHEDULES_DIR"
    echo "[CFG]  $(date '+%d/%m/%Y %H:%M:%S') — Token     : $TOKEN_FILE"
    echo "[CFG]  $(date '+%d/%m/%Y %H:%M:%S') — Access    : ${CFG_URL}"
    TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
    TADO_TOKEN_FILE="$TOKEN_FILE" \
    $PYTHON "$CFG_SCRIPT" --host "$CFG_HOST" --port "$CFG_PORT" \
        $( [ "$CONTEXT" != "mac" ] && echo "--no-browser" )

else

    # Single planning run (Mac, Linux, or HA SSH)
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Manual run ($CONTEXT) — v$VERSION"
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Schedules : $SCHEDULES_DIR"
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Token     : $TOKEN_FILE"
    init_schedules
    TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
    TADO_TOKEN_FILE="$TOKEN_FILE" \
    $PYTHON "$PLANNING_SCRIPT" ${PYTHON_ARGS[@]+"${PYTHON_ARGS[@]}"}

fi
