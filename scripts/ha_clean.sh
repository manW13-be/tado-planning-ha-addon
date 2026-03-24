#!/bin/bash
# =============================================================================
# scripts/ha_clean.sh — Nettoyage complet pour tester une fresh install
# Usage: ./scripts/ha_clean.sh [--keep-schedules] [--keep-token]
#
# Par défaut : supprime images, données, token ET schedules
# =============================================================================

ADDON_ID="fc4e2b3e_tado_planning"
DATA_DIR="/mnt/data/supervisor/addons/data/$ADDON_ID"
SCHEDULES_DIR="/homeassistant/tado-planning/schedules"
KEEP_SCHEDULES=false
KEEP_TOKEN=false

for arg in "$@"; do
    case $arg in
        --keep-schedules) KEEP_SCHEDULES=true ;;
        --keep-token)     KEEP_TOKEN=true ;;
    esac
done

echo "[CLEAN] Stopping addon..."
ha apps stop "$ADDON_ID" 2>/dev/null || true
sleep 2

echo "[CLEAN] Removing containers..."
CONTAINERS=$(docker ps -a | grep tado | awk '{print $1}')
[ -n "$CONTAINERS" ] && docker rm -f $CONTAINERS 2>/dev/null || true

echo "[CLEAN] Removing images..."
IMAGES=$(docker images | grep tado | awk '{print $3}')
[ -n "$IMAGES" ] && docker rmi $IMAGES 2>/dev/null || true

if [ "$KEEP_TOKEN" = false ]; then
    echo "[CLEAN] Removing token..."
    rm -f "$DATA_DIR/tado_refresh_token"
fi

if [ "$KEEP_SCHEDULES" = false ]; then
    echo "[CLEAN] Removing schedules..."
    rm -rf "$SCHEDULES_DIR"
fi

echo "[CLEAN] Removing addon data..."
rm -rf "$DATA_DIR"

echo ""
echo "[CLEAN] Done — summary:"
echo "  keep-schedules : $KEEP_SCHEDULES"
echo "  keep-token     : $KEEP_TOKEN"
echo ""
echo "[CLEAN] Ready for fresh install."
echo "  → Reinstall from HA UI, or run: ./scripts/ha_fetch_and_deploy.sh"
