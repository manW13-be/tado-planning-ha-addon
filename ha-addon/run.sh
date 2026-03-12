#!/bin/bash
set -e

SCRIPT="/config/tado/tado_planning.py"
OPTIONS_FILE="/data/options.json"

VERBOSITY=$(jq -r '.verbosity // 0' "$OPTIONS_FILE")

VFLAG=""
if [ "$VERBOSITY" -gt 0 ]; then
    VFLAG="-$(printf '%0.sv' $(seq 1 "$VERBOSITY"))"
fi

echo "[TADO] Add-on started — verbosity: $VERBOSITY"
echo "[TADO] Script : $SCRIPT"

if [ ! -f "$SCRIPT" ]; then
    echo "[ERROR] Script not found: $SCRIPT"
    echo "[ERROR] Please copy tado_planning.py to /config/tado/"
    exit 1
fi

while true; do
    echo ""
    echo "[TADO] Running at $(date '+%d/%m/%Y %H:%M')"
    python3 "$SCRIPT" $VFLAG || echo "[TADO] Script exited with error $?"
    SLEEP=$(( 3600 - $(date +%s) % 3600 ))
    echo "[TADO] Next run in ${SLEEP}s"
    sleep "$SLEEP"
done
