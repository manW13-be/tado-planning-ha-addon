#!/bin/bash

OPTIONS_FILE="/data/options.json"
VERBOSITY=$(jq -r '.verbosity // 0' "$OPTIONS_FILE")

VFLAG=""
if [ "$VERBOSITY" -gt 0 ] 2>/dev/null; then
    VFLAG="-$(printf '%0.sv' $(seq 1 "$VERBOSITY"))"
fi

echo "[TADO] Add-on started — verbosity: $VERBOSITY"
echo "[TADO] Script : /tado_planning.py"
echo "[TADO] Schedules : /data/schedules"

if [ ! -d "/data/schedules" ]; then
    echo "[ERROR] Schedules directory not found: /data/schedules"
    echo "[ERROR] Please copy your schedules to the add-on data directory"
    exit 1
fi

while true; do
    echo "[TADO] Running at $(date '+%d/%m/%Y %H:%M')"
    TADO_SCHEDULES_DIR="/data/schedules" python3 /tado_planning.py $VFLAG || echo "[TADO] Error $?"
    sleep $(( 3600 - $(date +%s) % 3600 ))
done
