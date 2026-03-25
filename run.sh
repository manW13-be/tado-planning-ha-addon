#!/bin/bash
# =============================================================================
# run.sh — Tado Planning entry point (universel)
#
# Modes détectés automatiquement :
#   1. Docker / HA addon  : boucle infinie, toutes les heures
#   2. Mac (Darwin)       : run unique, supporte les options
#   3. Linux manuel (SSH) : run unique, supporte les options
#
# Usage manuel (Mac ou Linux) :
#   ./run.sh                        # run simple, verbosity 0
#   ./run.sh -v                     # verbosity 1
#   ./run.sh -vv                    # verbosity 2
#   ./run.sh -vvv                   # verbosity 3
#   ./run.sh -vvvv                  # verbosity 4
#   ./run.sh -d 2026-04-10          # simuler une date
#   ./run.sh -c weekconfig.json     # forcer un weekconfig
#   ./run.sh -p planning.json       # forcer un planning
# =============================================================================

# ---------------------------------------------------------------------------
# Détection du contexte
# ---------------------------------------------------------------------------
if [ -f "/.dockerenv" ]; then
    CONTEXT="docker"
elif [ "$(uname)" = "Darwin" ]; then
    CONTEXT="mac"
else
    CONTEXT="linux"
fi

# ---------------------------------------------------------------------------
# Configuration par contexte
# ---------------------------------------------------------------------------
case "$CONTEXT" in
    docker)
        VERBOSITY=$(jq -r '.verbosity // 0' /data/options.json 2>/dev/null || echo "0")
        VFLAG=""
        if [ "$VERBOSITY" -gt 0 ] 2>/dev/null; then
            VFLAG="-$(printf '%0.sv' $(seq 1 "$VERBOSITY"))"
        fi
        SCHEDULES_DIR="/config/tado-planning/schedules"
        SCHEDULES_TEMPLATE="/schedules.tmpl"
        TOKEN_FILE="/data/tado_refresh_token"
        PYTHON="python3"
        SCRIPT="/tado_planning.py"
        ;;
    mac)
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        SCHEDULES_DIR="$SCRIPT_DIR/schedules"
        SCHEDULES_TEMPLATE="$SCRIPT_DIR/schedules.tmpl"
        TOKEN_FILE="$SCRIPT_DIR/tado_refresh_token"
        PYTHON=$(which python3.11 2>/dev/null || which python3)
        SCRIPT="$SCRIPT_DIR/tado_planning.py"
        VFLAG="$@"
        VERBOSITY=0
        ;;
    linux)
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        SCHEDULES_DIR="$SCRIPT_DIR/schedules"
        SCHEDULES_TEMPLATE="$SCRIPT_DIR/schedules.tmpl"
        TOKEN_FILE="/data/tado_refresh_token"
        PYTHON="python3"
        SCRIPT="$SCRIPT_DIR/tado_planning.py"
        VFLAG="$@"
        VERBOSITY=0
        ;;
esac

# ---------------------------------------------------------------------------
# Initialisation des schedules depuis schedules.tmpl si absent ou vide
# ---------------------------------------------------------------------------
init_schedules() {
    if [ ! -d "$SCHEDULES_DIR" ] || [ -z "$(ls -A "$SCHEDULES_DIR" 2>/dev/null)" ]; then
        if [ -d "$SCHEDULES_TEMPLATE" ]; then
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — schedules/ not found — initializing from schedules.tmpl/..."
            mkdir -p "$SCHEDULES_DIR"
            cp "$SCHEDULES_TEMPLATE"/* "$SCHEDULES_DIR/"
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — schedules/ initialized ($(ls "$SCHEDULES_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ') files)"
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — ⚠ Review and adapt the schedule files before next run"
        else
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — ERROR: schedules/ not found and no schedules.tmpl/ available"
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Create $SCHEDULES_DIR with your schedule files"
            exit 1
        fi
    else
        echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Schedules found ($(ls "$SCHEDULES_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ') files)"
    fi
}

# ---------------------------------------------------------------------------
# Fonction next run time (compatible Mac et Linux)
# ---------------------------------------------------------------------------
next_run_time() {
    NEXT=$(( 3600 - $(date +%s) % 3600 ))
    if [ "$(uname)" = "Darwin" ]; then
        date -v+${NEXT}S '+%d/%m/%Y %H:%M:%S'
    else
        date -d "+${NEXT} seconds" '+%d/%m/%Y %H:%M:%S'
    fi
}

# ---------------------------------------------------------------------------
# Mode Docker — boucle infinie
# ---------------------------------------------------------------------------
if [ "$CONTEXT" = "docker" ]; then

    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Add-on started — verbosity: $VERBOSITY"

    init_schedules

    # Info token
    if [ ! -f "$TOKEN_FILE" ]; then
        echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — No token found — auth URL will appear below"
    fi

    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Starting main loop..."

    while true; do
        echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Running..."
        TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
        TADO_TOKEN_FILE="$TOKEN_FILE" \
        $PYTHON "$SCRIPT" $VFLAG 2>&1 \
            || echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Script exited with error $?"
        NEXT_TIME=$(next_run_time)
        echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Next run at $NEXT_TIME"
        sleep $(( 3600 - $(date +%s) % 3600 ))
    done

# ---------------------------------------------------------------------------
# Mode Mac ou Linux manuel — run unique
# ---------------------------------------------------------------------------
else

    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Manual run ($CONTEXT)"
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Schedules : $SCHEDULES_DIR"
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Token     : $TOKEN_FILE"

    init_schedules

    TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
    TADO_TOKEN_FILE="$TOKEN_FILE" \
    $PYTHON "$SCRIPT" $VFLAG

fi
