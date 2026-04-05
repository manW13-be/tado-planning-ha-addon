#!/bin/bash
# =============================================================================
# run.sh — Tado Planning entry point (universel)
#
# Contextes détectés automatiquement :
#   - Dans le container Docker (HA add-on)
#   - macOS (Darwin)
#   - Linux / HA SSH (hors container) → re-exécute via docker exec
#
# Usage :
#   ./run.sh                        # run unique, verbosity 0
#   ./run.sh --loop                 # boucle infinie toutes les heures (HA add-on)
#   ./run.sh -v                     # verbosity 1
#   ./run.sh -vv                    # verbosity 2
#   ./run.sh -vvv                   # verbosity 3
#   ./run.sh -vvvv                  # verbosity 4
#   ./run.sh -d 2026-04-10          # simuler une date
#   ./run.sh -c weekconfig.json     # forcer un weekconfig
#   ./run.sh -p planning.json       # forcer un planning
#
# Le Dockerfile appelle : CMD ["/run.sh", "--loop"]
# =============================================================================

set -euo pipefail

ADDON_CONTAINER="addon_fc4e2b3e_tado_planning"
ADDON_ID="fc4e2b3e_tado_planning"

# ---------------------------------------------------------------------------
# Détection du contexte
# ---------------------------------------------------------------------------
if [ -f "/tado-planning.py" ]; then
    CONTEXT="docker"
elif [ "$(uname)" = "Darwin" ]; then
    CONTEXT="mac"
else
    CONTEXT="linux"
fi

# ---------------------------------------------------------------------------
# Mode Linux/HA SSH — run directly with persistent venv
# ---------------------------------------------------------------------------
if [ "$CONTEXT" = "linux" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
    SCHEDULES_DIR="$PROJECT_DIR/schedules"
    SCHEDULES_TMPL="$PROJECT_DIR/schedules.tmpl"
    TOKEN_FILE="$PROJECT_DIR/tado_refresh_token"
    SCRIPT="$SCRIPT_DIR/tado-planning.py"

    # Persistent venv in /config/ — survives HA reboots
    VENV_DIR="/config/tado-planning/venv"
    if [ ! -f "$VENV_DIR/bin/python3" ]; then
        echo "[TADO] Creating Python venv at $VENV_DIR..."
        mkdir -p "$(dirname "$VENV_DIR")"
        python3 -m venv "$VENV_DIR"
    fi
    PYTHON="$VENV_DIR/bin/python3"

    # Check and install only missing packages
    MISSING=()
    "$PYTHON" -c "import PyTado" 2>/dev/null || MISSING+=("python-tado>=0.18")
    if [ ${#MISSING[@]} -gt 0 ]; then
        echo "[TADO] Installing missing packages: ${MISSING[*]}"
        "$VENV_DIR/bin/pip" install --quiet "${MISSING[@]}"
        echo "[TADO] Done."
    fi

    VERSION=$(jq -r '.version' "$SCRIPT_DIR/config.json" 2>/dev/null || echo "unknown")
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Manual run (linux) — v$VERSION"
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Schedules : $SCHEDULES_DIR"
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Token     : $TOKEN_FILE"

    init_schedules() {
        if [ ! -d "$SCHEDULES_DIR" ] || [ -z "$(ls -A "$SCHEDULES_DIR" 2>/dev/null)" ]; then
            if [ -d "$SCHEDULES_TMPL" ]; then
                echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Initializing schedules from schedules.tmpl/..."
                mkdir -p "$SCHEDULES_DIR"
                cp "$SCHEDULES_TMPL"/* "$SCHEDULES_DIR/"
            else
                echo "[TADO] ERROR: schedules/ not found and no schedules.tmpl/ available"
                exit 1
            fi
        else
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Schedules: $(ls "$SCHEDULES_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ') file(s)"
        fi
    }
    init_schedules

    TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
    TADO_TOKEN_FILE="$TOKEN_FILE" \
    $PYTHON "$SCRIPT" "$@"
    exit 0
fi

# ---------------------------------------------------------------------------
# Configuration — Docker (dans le container) ou Mac
# ---------------------------------------------------------------------------
case "$CONTEXT" in
    docker)
        VERBOSITY=$(jq -r '.verbosity // 0' /data/options.json 2>/dev/null || echo "0")
        SCHEDULES_DIR="/config/tado-planning/schedules"
        SCHEDULES_TMPL="/schedules.tmpl"
        TOKEN_FILE="/data/tado_refresh_token"
        PYTHON="python3"
        SCRIPT="/tado-planning.py"
        ;;
    mac)
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
        SCHEDULES_DIR="$PROJECT_DIR/schedules"
        SCHEDULES_TMPL="$PROJECT_DIR/schedules.tmpl"
        TOKEN_FILE="$PROJECT_DIR/tado_refresh_token"
        PYTHON=$(which python3.11 2>/dev/null || which python3)
        SCRIPT="$SCRIPT_DIR/tado-planning.py"
        VERBOSITY=0
        ;;
esac

# ---------------------------------------------------------------------------
# Extraction de --loop et construction des flags Python
# ---------------------------------------------------------------------------
LOOP=false
PYTHON_ARGS=()
for arg in "$@"; do
    if [ "$arg" = "--loop" ]; then
        LOOP=true
    else
        PYTHON_ARGS+=("$arg")
    fi
done

# En mode loop Docker, utiliser le verbosity de options.json
if [ "$LOOP" = true ] && [ "$CONTEXT" = "docker" ]; then
    VFLAG=""
    if [ "$VERBOSITY" -gt 0 ] 2>/dev/null; then
        VFLAG="-$(printf '%0.sv' $(seq 1 "$VERBOSITY"))"
    fi
    PYTHON_ARGS=($VFLAG)
fi

# ---------------------------------------------------------------------------
# Initialisation des schedules depuis schedules.tmpl si absent ou vide
# ---------------------------------------------------------------------------
init_schedules() {
    if [ ! -d "$SCHEDULES_DIR" ] || [ -z "$(ls -A "$SCHEDULES_DIR" 2>/dev/null)" ]; then
        if [ -d "$SCHEDULES_TMPL" ]; then
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — schedules/ not found — initializing from schedules.tmpl/..."
            mkdir -p "$SCHEDULES_DIR"
            cp "$SCHEDULES_TMPL"/* "$SCHEDULES_DIR/"
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — schedules/ initialized ($(ls "$SCHEDULES_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ') files)"
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — ⚠ Review and adapt schedule files before next run"
        else
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — ERROR: schedules/ not found and no schedules.tmpl/ available"
            echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Run: ./scripts/init_schedules.sh"
            exit 1
        fi
    else
        echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Schedules: $(ls "$SCHEDULES_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ') file(s)"
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
# Version
# ---------------------------------------------------------------------------
if [ "$CONTEXT" = "docker" ]; then
    VERSION=$(jq -r '.version' /config/tado-planning/config.json 2>/dev/null || echo "unknown")
else
    VERSION=$(jq -r '.version' "$SCRIPT_DIR/config.json" 2>/dev/null || echo "unknown")
fi

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if [ "$LOOP" = true ]; then
    # --- Mode boucle (HA add-on) ---
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Add-on started — v$VERSION — verbosity: $VERBOSITY"
    init_schedules
    if [ ! -f "$TOKEN_FILE" ]; then
        echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — No token found — auth URL will appear on first run"
    fi
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Starting main loop..."
    while true; do
        echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Running..."
        TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
        TADO_TOKEN_FILE="$TOKEN_FILE" \
        $PYTHON "$SCRIPT" ${PYTHON_ARGS[@]+"${PYTHON_ARGS[@]}"} 2>&1 \
            || echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Script exited with error $?"
        echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Next run at $(next_run_time)"
        sleep $(( 3600 - $(date +%s) % 3600 ))
    done

else
    # --- Mode run unique (manuel) ---
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Manual run ($CONTEXT) — v$VERSION"
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Schedules : $SCHEDULES_DIR"
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Token     : $TOKEN_FILE"
    init_schedules
    TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
    TADO_TOKEN_FILE="$TOKEN_FILE" \
    $PYTHON "$SCRIPT" ${PYTHON_ARGS[@]+"${PYTHON_ARGS[@]}"}
fi
