#!/bin/bash
# =============================================================================
# scripts/ha_debug.sh — Run manuel dans le container HA
# Usage: ./scripts/ha_debug.sh [-v] [-vv] [-vvv] [-vvvv]
#                               [-d YYYY-MM-DD] [-c config] [-p planning]
# =============================================================================

CONTAINER=$(docker ps | grep tado | awk '{print $1}')

if [ -z "$CONTAINER" ]; then
    echo "[DEBUG] Addon container not running — starting addon first..."
    ha apps start fc4e2b3e_tado_planning
    sleep 3
    CONTAINER=$(docker ps | grep tado | awk '{print $1}')
fi

echo "[DEBUG] Running in container $CONTAINER — args: $*"
docker exec "$CONTAINER" /run.sh "$@"
