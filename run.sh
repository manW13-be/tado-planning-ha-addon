#!/bin/bash
# =============================================================================
# Tado Planning — Add-on entry point
# =============================================================================

OPTIONS_FILE="/data/options.json"
VERBOSITY=$(jq -r '.verbosity // 0' "$OPTIONS_FILE" 2>/dev/null || echo "0")
VFLAG=""
if [ "$VERBOSITY" -gt 0 ] 2>/dev/null; then
    VFLAG="-$(printf '%0.sv' $(seq 1 "$VERBOSITY"))"
fi

echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Add-on started — verbosity: $VERBOSITY"

# -----------------------------------------------------------------------------
# 1. Initialisation des schedules (copie depuis l'image si absent ou vide)
# -----------------------------------------------------------------------------
SCHEDULES_DIR="/config/tado-planning/schedules"

if [ ! -d "$SCHEDULES_DIR" ] || [ -z "$(ls -A "$SCHEDULES_DIR" 2>/dev/null)" ]; then
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Initializing schedules from defaults..."
    mkdir -p "$SCHEDULES_DIR"
    cp /default_schedules/* "$SCHEDULES_DIR/"
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Schedules initialized in $SCHEDULES_DIR"
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Edit your schedules via Samba: config/tado-planning/schedules/"
else
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Schedules found ($(ls "$SCHEDULES_DIR"/*.json 2>/dev/null | wc -l) files)"
fi

# -----------------------------------------------------------------------------
# 2. Vérification du token d'authentification
# -----------------------------------------------------------------------------
if [ ! -f "/data/tado_refresh_token" ]; then
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — No token found — authentication required on first run"
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — The script will display the auth URL in the logs below"
fi

# -----------------------------------------------------------------------------
# 3. Boucle principale
# -----------------------------------------------------------------------------
echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Starting main loop..."

while true; do
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Running..."
    TADO_SCHEDULES_DIR="$SCHEDULES_DIR" \
    TADO_TOKEN_FILE="/data/tado_refresh_token" \
    python3 /tado_planning.py $VFLAG 2>&1 || echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Script exited with error $?"
    NEXT=$(( 3600 - $(date +%s) % 3600 ))
    echo "[TADO] $(date '+%d/%m/%Y %H:%M:%S') — Next run in ${NEXT}s"
    sleep $NEXT
done