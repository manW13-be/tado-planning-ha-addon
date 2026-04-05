#!/bin/bash
# =============================================================================
# scripts/ha_deploy.sh — Rebuild image Docker + restart add-on (HA SSH)
#
# À utiliser après fetch.sh quand on veut forcer un rebuild immédiat,
# sans attendre que le store HA détecte la nouvelle version.
#
# Usage :
#   ./scripts/ha_deploy.sh
# =============================================================================

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ADDON_ID="fc4e2b3e_tado_planning"
cd "$REPO_DIR"

VERSION=$(jq -r '.version' run/config.json)
IMAGE="fc4e2b3e/aarch64-addon-tado_planning:$VERSION"

# --- Nettoyage des anciennes images ------------------------------------------
echo "[DEPLOY] Cleaning old images..."
OLD_IMAGES=$(docker images | grep "fc4e2b3e/aarch64-addon-tado_planning" | awk '{print $1":"$2}')
if [ -n "$OLD_IMAGES" ]; then
    ha apps stop "$ADDON_ID" 2>/dev/null || true
    sleep 2
    docker rm -f $(docker ps -a | grep tado | awk '{print $1}') 2>/dev/null || true
    for img in $OLD_IMAGES; do
        docker rmi "$img" 2>/dev/null && echo "[DEPLOY] Removed $img" || true
    done
else
    echo "[DEPLOY] No old images to clean."
fi

# --- Build -------------------------------------------------------------------
echo "[DEPLOY] Building image $IMAGE..."
docker build --no-cache -f run/Dockerfile -t "$IMAGE" "$REPO_DIR"
echo "[DEPLOY] Build OK"

# --- Restart -----------------------------------------------------------------
echo "[DEPLOY] Restarting add-on..."
ha apps restart "$ADDON_ID"

echo "[DEPLOY] Done — v$VERSION — waiting for logs..."
sleep 3
ha apps logs "$ADDON_ID" | tail -20
