#!/bin/bash

OPTIONS_FILE="/data/options.json"
VERBOSITY=$(jq -r '.verbosity // 0' "$OPTIONS_FILE")

VFLAG=""
if [ "$VERBOSITY" -gt 0 ] 2>/dev/null; then
    VFLAG="-$(printf '%0.sv' $(seq 1 "$VERBOSITY"))"
fi

echo "[TADO] Add-on started — verbosity: $VERBOSITY"

# Cherche le script dans plusieurs emplacements possibles
for BASE in /config /mnt/data/supervisor/homeassistant /homeassistant; do
    if [ -f "$BASE/tado/tado_planning.py" ]; then
        SCRIPT="$BASE/tado/tado_planning.py"
        SCHEDULES="$BASE/tado/schedules"
        break
    fi
done

if [ -z "$SCRIPT" ]; then
    echo "[ERROR] Script not found in any known location"
    echo "[ERROR] Tried: /config /mnt/data/supervisor/homeassistant /homeassistant"
    exit 1
fi

echo "[TADO] Script : $SCRIPT"

while true; do
    echo "[TADO] Running at $(date '+%d/%m/%Y %H:%M')"
    TADO_SCHEDULES_DIR="$SCHEDULES" python3 "$SCRIPT" $VFLAG || echo "[TADO] Error $?"
    sleep $(( 3600 - $(date +%s) % 3600 ))
done
