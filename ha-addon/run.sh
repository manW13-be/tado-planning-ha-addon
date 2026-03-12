#!/bin/bash
set -e

SCRIPT="/config/tado/tado_planning.py"
OPTIONS_FILE="/data/options.json"

# Lecture de la verbosité depuis les options de l'add-on
VERBOSITY=$(jq -r '.verbosity' "$OPTIONS_FILE")

# Construction du flag -v/-vv/-vvv/-vvvv
VFLAG=""
if [ "$VERBOSITY" -gt 0 ]; then
    VFLAG="-$(printf '%0.sv' $(seq 1 "$VERBOSITY"))"
fi

echo "[TADO] Add-on started — verbosity: $VERBOSITY"
echo "[TADO] Script : $SCRIPT"
echo "[TADO] Schedules : /config/tado/schedules"

# Vérification que le script existe
if [ ! -f "$SCRIPT" ]; then
    echo "[ERROR] Script not found: $SCRIPT"
    echo "[ERROR] Please copy tado_planning.py to /config/tado/"
    exit 1
fi

# Boucle principale — exécution toutes les heures pile
while true; do
    echo ""
    echo "[TADO] Running at $(date '+%d/%m/%Y %H:%M')"
    python3 "$SCRIPT" $VFLAG || echo "[TADO] Script exited with error $?"
    # Attendre jusqu'à la prochaine heure pile
    SLEEP=$(( 3600 - $(date +%s) % 3600 ))
    echo "[TADO] Next run in ${SLEEP}s ($(date -d "+${SLEEP} seconds" '+%H:%M' 2>/dev/null || date -v +${SLEEP}S '+%H:%M'))"
    sleep "$SLEEP"
done
