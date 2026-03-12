#!/bin/bash

OPTIONS_FILE="/data/options.json"
VERBOSITY=$(jq -r '.verbosity // 0' "$OPTIONS_FILE")

VFLAG=""
if [ "$VERBOSITY" -gt 0 ] 2>/dev/null; then
    VFLAG="-$(printf '%0.sv' $(seq 1 "$VERBOSITY"))"
fi

echo "[TADO] Add-on started — verbosity: $VERBOSITY"
echo "[TADO] Listing root directories:"
ls /
echo "[TADO] Listing /config/:"
ls /config/ 2>&1 || echo "Cannot access /config/"
echo "[TADO] Listing /homeassistant/:"
ls /homeassistant/ 2>&1 || echo "Cannot access /homeassistant/"

SCRIPT="/config/tado/tado_planning.py"
if [ ! -f "$SCRIPT" ]; then
    echo "[ERROR] Script not found: $SCRIPT"
    exit 1
fi

while true; do
    echo "[TADO] Running at $(date '+%d/%m/%Y %H:%M')"
    python3 "$SCRIPT" $VFLAG || echo "[TADO] Error $?"
    sleep $(( 3600 - $(date +%s) % 3600 ))
done
