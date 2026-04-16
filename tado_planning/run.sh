#!/bin/bash
# =============================================================================
# tado_planning/run.sh — Universal entry point
#
# CONTEXTS (auto-detected):
#   mac-launchd    macOS, started by launchd (prod)
#   mac-shell      macOS, interactive shell (dev/test)
#   ha-docker-prod inside prod Docker container (supervisor-managed)
#   ha-docker-test inside test Docker container (docker_test_start.sh)
#   ha-shell       HA Linux SSH, direct local execution (dev/test)
#
# MODES:
#   (no flag)   single scheduler run then exit
#   --loop      scheduler loop + Flask configurator (prod container only)
#   --cfg       Flask configurator only
#
# CONFLICT RULES:
#   mac-launchd    → no check (launchd serialises)
#   mac-shell      → reject if launchd agent loaded
#   ha-shell       → reject if any addon container running (prod or test)
#   ha-docker-test → reject if prod container running
#   ha-docker-prod → reject if test container running
#
# EXAMPLES:
#   ./tado_planning/run.sh              # single run (mac or ha-shell)
#   ./tado_planning/run.sh --cfg        # start Flask configurator
#   ./tado_planning/run.sh -vv -d 2026-04-10   # simulate date
#   /run.sh --loop                      # inside prod container (CMD)
# =============================================================================

set -euo pipefail

PROD_CONTAINER="addon_fc4e2b3e_tado_planning"
TEST_CONTAINER="addon_test_tado_planning"
LAUNCHD_LABEL="com.tado-planning"

# ---------------------------------------------------------------------------
# Context detection
#
# /.dockerenv exists on HAOS shell too (it runs inside a container itself),
# so we cannot use it alone to detect "inside addon container".
# Reliable signal: the Dockerfile places run.sh at /run.sh — if this script
# is running from /, we are inside an addon container; otherwise we are on
# the HA SSH shell (or Mac).
# ---------------------------------------------------------------------------
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$SELF_DIR" = "/" ]; then
    # Inside a Docker container (run.sh placed at / by Dockerfile)
    if hostname 2>/dev/null | grep -q "fc4e2b3e"; then
        CONTEXT="ha-docker-prod"
    else
        CONTEXT="ha-docker-test"
    fi
elif [ "$(uname)" = "Darwin" ]; then
    if [ "${LAUNCHED_BY_LAUNCHD:-}" = "1" ]; then
        CONTEXT="mac-launchd"
    else
        CONTEXT="mac-shell"
    fi
else
    CONTEXT="ha-shell"
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  {
    local msg="[TADO] $(date '+%d/%m/%Y %H:%M:%S') — $*"
    echo "$msg"
    if [ -n "${LOG_FILE:-}" ]; then
        echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
    fi
}
die()  { echo "[TADO] ERROR: $*" >&2; exit 1; }
container_running() { docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${1}$"; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
LOOP=false
RUN_CFG=false
PYTHON_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --loop) LOOP=true ;;
        --cfg)  RUN_CFG=true ;;
        --run)  ;;   # explicit single-run flag — no-op (default behaviour)
        *)      PYTHON_ARGS+=("$arg") ;;
    esac
done

# Validate: --loop and --cfg are mutually exclusive
if [ "$LOOP" = true ] && [ "$RUN_CFG" = true ]; then
    die "--loop and --cfg are mutually exclusive."
fi

# --loop only makes sense inside a container
if [ "$LOOP" = true ] && [[ "$CONTEXT" != ha-docker-* ]]; then
    die "--loop is only valid inside a Docker container. Use --cfg or no flag for local runs."
fi

# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------
case "$CONTEXT" in

    mac-shell)
        if launchctl list 2>/dev/null | grep -q "$LAUNCHD_LABEL"; then
            die "macOS launchd prod agent is running.
  Stop it first: ./scripts/uninstall_launchd.sh
  Or:            launchctl unload ~/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"
        fi
        ;;

    ha-shell)
        if container_running "$PROD_CONTAINER"; then
            die "Prod container '$PROD_CONTAINER' is running.
  Stop the add-on from the HA UI first, or use docker_test_start.sh to run via test container."
        fi
        if container_running "$TEST_CONTAINER"; then
            die "Test container '$TEST_CONTAINER' is running.
  Stop it first: ./scripts/docker_test_stop.sh"
        fi
        ;;

    ha-docker-test)
        if container_running "$PROD_CONTAINER" 2>/dev/null; then
            die "Prod container '$PROD_CONTAINER' is running alongside the test container."
        fi
        ;;

    ha-docker-prod)
        if container_running "$TEST_CONTAINER" 2>/dev/null; then
            die "Test container '$TEST_CONTAINER' is running alongside the prod container.
  Stop it first: ./scripts/docker_test_stop.sh (from HA SSH)"
        fi
        ;;

    mac-launchd)
        # No conflict check — launchd serialises invocations
        ;;
esac

# ---------------------------------------------------------------------------
# Path configuration
# ---------------------------------------------------------------------------
case "$CONTEXT" in

    mac-shell|mac-launchd)
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
        SCHEDULES_DIR="$PROJECT_DIR/schedules"
        TOKEN_FILE="$PROJECT_DIR/tado_refresh_token"
        PYTHON=$(which python3.11 2>/dev/null || which python3)
        PLANNING_SCRIPT="$SCRIPT_DIR/tado-planning-run.py"
        CFG_SCRIPT="$SCRIPT_DIR/tado-planning-cfg.py"
        CFG_PORT="${CFG_PORT:-8080}"
        CFG_HOST="127.0.0.1"
        VERBOSITY=0
        VERSION=$(jq -r '.version' "$SCRIPT_DIR/config.json" 2>/dev/null || echo "unknown")
        ;;

    ha-shell)
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
        SCHEDULES_DIR="/config/tado-planning/schedules"
        TOKEN_FILE="/config/tado-planning/tado_refresh_token"
        CFG_PORT="${CFG_PORT:-8099}"
        CFG_HOST="0.0.0.0"
        VERBOSITY=0
        VERSION=$(jq -r '.version' "$SCRIPT_DIR/config.json" 2>/dev/null || echo "unknown")
        PLANNING_SCRIPT="$SCRIPT_DIR/tado-planning-run.py"
        CFG_SCRIPT="$SCRIPT_DIR/tado-planning-cfg.py"

        # Persistent venv in /config/ — survives HA reboots
        VENV_DIR="/config/tado-planning/venv"
        if [ ! -f "$VENV_DIR/bin/python3" ]; then
            log "Creating Python venv at $VENV_DIR..."
            mkdir -p "$(dirname "$VENV_DIR")"
            python3 -m venv "$VENV_DIR"
        fi
        PYTHON="$VENV_DIR/bin/python3"

        MISSING=()
        "$PYTHON" -c "import PyTado"   2>/dev/null || MISSING+=("python-tado>=0.18")
        "$PYTHON" -c "import flask"    2>/dev/null || MISSING+=("flask")
        "$PYTHON" -c "import requests" 2>/dev/null || MISSING+=("requests")
        if [ ${#MISSING[@]} -gt 0 ]; then
            log "Installing: ${MISSING[*]}"
            "$VENV_DIR/bin/pip" install --quiet "${MISSING[@]}"
        fi
        ;;

    ha-docker-prod|ha-docker-test)
        VERBOSITY=$(jq -r '.verbosity // 0' /data/options.json 2>/dev/null || echo "0")
        SCHEDULES_DIR="/config/tado-planning/schedules"
        TOKEN_FILE="/config/tado-planning/tado_refresh_token"
        PYTHON="python3"
        PLANNING_SCRIPT="/tado-planning-run.py"
        CFG_SCRIPT="/tado-planning-cfg.py"
        CFG_PORT=8099
        CFG_HOST="0.0.0.0"
        VERSION=$(jq -r '.version' /config.json.addon 2>/dev/null || echo "unknown")
        ;;
esac

# ---------------------------------------------------------------------------
# Schedule initialisation
# ---------------------------------------------------------------------------
init_schedules() {
    mkdir -p "$SCHEDULES_DIR"
    local count
    count=$(ls "$SCHEDULES_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ')
    log "Schedules: ${count} file(s)"
}

# ---------------------------------------------------------------------------
# Next run time (portable Mac + Linux)
# ---------------------------------------------------------------------------
next_run_time() {
    local INTERVAL_SEC="${1:-3600}"
    local NEXT=$(( INTERVAL_SEC - $(date +%s) % INTERVAL_SEC ))
    if [ "$(uname)" = "Darwin" ]; then
        date -v+${NEXT}S '+%d/%m/%Y %H:%M:%S'
    else
        date -d "+${NEXT} seconds" '+%d/%m/%Y %H:%M:%S'
    fi
}

read_loop_interval() {
    # Read loop_interval (minutes) from settings.json, fallback to 60
    local SETTINGS="${SCHEDULES_DIR}/settings.json"
    local MINUTES=60
    if [ -f "$SETTINGS" ]; then
        MINUTES=$(python3 -c "
import json, sys
try:
    d = json.load(open('${SETTINGS}'))
    v = int(d.get('loop_interval', 60))
    print(max(1, v))
except Exception:
    print(60)
" 2>/dev/null || echo 60)
    fi
    echo $(( MINUTES * 60 ))
}

# ---------------------------------------------------------------------------
# CFG access URL
# ---------------------------------------------------------------------------
get_cfg_url() {
    local HOST=""
    if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
        HOST=$(curl -sf \
            -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
            http://supervisor/core/api/config 2>/dev/null \
            | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('internal_url',''))" \
            2>/dev/null | sed 's|https\?://||' | cut -d'/' -f1 || true)
    fi
    if [ -z "$HOST" ]; then
        if [ "$(uname)" = "Darwin" ]; then
            HOST=$(hostname -f 2>/dev/null || echo "localhost")
        else
            HOST=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
        fi
    fi
    echo "http://${HOST}:${CFG_PORT}"
}

# ---------------------------------------------------------------------------
# Announce
# ---------------------------------------------------------------------------
log "Context : $CONTEXT | v${VERSION}"
log "Schedules: $SCHEDULES_DIR"
log "Token    : $TOKEN_FILE"

# ---------------------------------------------------------------------------
# Loop control files (in schedules dir)
# ---------------------------------------------------------------------------
LOOP_STATUS_FILE=""
LOOP_TRIGGER_FILE=""
LOG_FILE=""

set_loop_files() {
    LOOP_STATUS_FILE="${SCHEDULES_DIR}/loop_status.json"
    LOOP_TRIGGER_FILE="${SCHEDULES_DIR}/loop_trigger"
    LOG_FILE="${SCHEDULES_DIR}/tado-planning.log"
}

rotate_log() {
    # Keep last 500KB — rename to .log.1 if exceeded
    local MAX=512000
    if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt $MAX ]; then
        mv "$LOG_FILE" "${LOG_FILE}.1"
    fi
}

tee_log() {
    # Tee stdin to LOG_FILE (append) and pass through to stdout
    rotate_log
    tee -a "$LOG_FILE"
}

write_loop_status() {
    local interval_sec="$1"
    local next_ts="$2"
    cat > "$LOOP_STATUS_FILE" << JSON
{
  "pid": $$,
  "interval_sec": ${interval_sec},
  "interval_min": $(( interval_sec / 60 )),
  "last_run": "$(date '+%Y-%m-%d %H:%M:%S')",
  "next_run": "${next_ts}"
}
JSON
}

check_trigger() {
    if [ -f "$LOOP_TRIGGER_FILE" ]; then
        rm -f "$LOOP_TRIGGER_FILE"
        return 0  # trigger found
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

if [ "$LOOP" = true ]; then
    # -------------------------------------------------------------------------
    # --loop: prod/test container — start both scheduler loop and Flask
    # -------------------------------------------------------------------------
    VFLAG=""
    if [ "${VERBOSITY:-0}" -gt 0 ] 2>/dev/null; then
        VFLAG="-$(printf '%0.sv' $(seq 1 "$VERBOSITY"))"
    fi
    PYTHON_ARGS=($VFLAG)

    log "Mode: loop (scheduler + configurator)"
    init_schedules

    CFG_URL=$(get_cfg_url)
    log "Starting configurator on ${CFG_HOST}:${CFG_PORT} — $CFG_URL"
    TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
    TADO_TOKEN_FILE="$TOKEN_FILE" \
    $PYTHON "$CFG_SCRIPT" --host "$CFG_HOST" --port "$CFG_PORT" --no-browser &
    CFG_PID=$!

    set_loop_files
    trap 'rm -f "$LOOP_STATUS_FILE" "$LOOP_TRIGGER_FILE"; exit' INT TERM EXIT
    log "Starting scheduler loop..."
    while true; do
        LOOP_INTERVAL_SEC=$(read_loop_interval)
        LOOP_INTERVAL_MIN=$(( LOOP_INTERVAL_SEC / 60 ))
        log "Running scheduler (interval: ${LOOP_INTERVAL_MIN}min)..."
        TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
        TADO_TOKEN_FILE="$TOKEN_FILE" \
        $PYTHON "$PLANNING_SCRIPT" ${PYTHON_ARGS[@]+"${PYTHON_ARGS[@]}"} 2>&1 \
            | tee_log || true
        # Re-read interval after run (may have changed via settings)
        LOOP_INTERVAL_SEC=$(read_loop_interval)
        SLEEP_SEC=$(( LOOP_INTERVAL_SEC - $(date +%s) % LOOP_INTERVAL_SEC ))
        NEXT_RUN_TS=$(next_run_time "$LOOP_INTERVAL_SEC")
        log "Next run at ${NEXT_RUN_TS} (in ${SLEEP_SEC}s)"
        write_loop_status "$LOOP_INTERVAL_SEC" "$NEXT_RUN_TS"
        # Sleep in 5s increments to stay responsive to trigger
        SLEPT=0
        while [ $SLEPT -lt $SLEEP_SEC ]; do
            if check_trigger; then
                log "Manual trigger received — running scheduler immediately"
                break
            fi
            CHUNK=5
            if [ $(( SLEEP_SEC - SLEPT )) -lt $CHUNK ]; then
                CHUNK=$(( SLEEP_SEC - SLEPT ))
            fi
            sleep $CHUNK
            SLEPT=$(( SLEPT + CHUNK ))
        done
    done

elif [ "$RUN_CFG" = true ]; then
    # -------------------------------------------------------------------------
    # --cfg: Flask configurator only
    # -------------------------------------------------------------------------
    log "Mode: cfg"
    init_schedules
    LOG_FILE="${SCHEDULES_DIR}/tado-planning.log"
    CFG_URL=$(get_cfg_url)
    log "Configurator on ${CFG_HOST}:${CFG_PORT} — $CFG_URL"
    TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
    TADO_TOKEN_FILE="$TOKEN_FILE" \
    $PYTHON "$CFG_SCRIPT" --host "$CFG_HOST" --port "$CFG_PORT" \
        $( [ "$CONTEXT" != "mac-shell" ] && echo "--no-browser" )

else
    # -------------------------------------------------------------------------
    # Single scheduler run
    # -------------------------------------------------------------------------
    log "Mode: run"
    init_schedules
    LOG_FILE="${SCHEDULES_DIR}/tado-planning.log"
    rotate_log
    TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
    TADO_TOKEN_FILE="$TOKEN_FILE" \
    $PYTHON "$PLANNING_SCRIPT" ${PYTHON_ARGS[@]+"${PYTHON_ARGS[@]}"} 2>&1 | tee -a "$LOG_FILE"

fi
