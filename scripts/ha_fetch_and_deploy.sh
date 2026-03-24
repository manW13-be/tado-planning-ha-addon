#!/bin/bash
# =============================================================================
# scripts/ha_fetch_and_deploy.sh — Pull GitHub + rebuild Docker + restart addon
# Usage: ./scripts/ha_fetch_and_deploy.sh
# =============================================================================

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ADDON_ID="fc4e2b3e_tado_planning"
cd "$REPO_DIR"

echo "[DEPLOY] Pulling from GitHub..."
git pull origin main

# Copier .gitignore → gitignore (éditable depuis Samba)
if [ -f ".gitignore" ]; then
    cp .gitignore gitignore
    echo "[DEPLOY] .gitignore → gitignore"
fi

VERSION=$(jq -r '.version' config.json)
IMAGE="fc4e2b3e/aarch64-addon-tado_planning:$VERSION"

# Supprimer les anciennes images tado
echo "[DEPLOY] Cleaning old images..."
OLD_IMAGES=$(docker images | grep "fc4e2b3e/aarch64-addon-tado_planning" | awk '{print $1":"$2}')
if [ -n "$OLD_IMAGES" ]; then
    ha apps stop "$ADDON_ID" 2>/dev/null || true
    sleep 2
    docker rm -f $(docker ps -a | grep tado | awk '{print $1}') 2>/dev/null || true
    for img in $OLD_IMAGES; do
        docker rmi "$img" 2>/dev/null && echo "[DEPLOY] Removed $img" || true
    done
fi

echo "[DEPLOY] Building image $IMAGE..."
docker build --no-cache -t "$IMAGE" "$REPO_DIR"
echo "[DEPLOY] Build OK"

echo "[DEPLOY] Restarting addon..."
ha apps restart "$ADDON_ID"

echo "[DEPLOY] Done — v$VERSION — waiting for logs..."
sleep 3
ha apps logs "$ADDON_ID" | tail -15
