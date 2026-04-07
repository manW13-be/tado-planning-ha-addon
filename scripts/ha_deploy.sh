#!/bin/bash
# =============================================================================
# scripts/ha_deploy.sh — Rebuild Docker image + redeploy addon (HA SSH)
#
# Bypasses 'ha apps restart' which reuses HA's cached image.
# Uses docker run directly with the same mounts as the HA supervisor.
#
# Usage:
#   ./scripts/ha_deploy.sh
# =============================================================================

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

ADDON_ID="fc4e2b3e_tado_planning"
CONTAINER="addon_fc4e2b3e_tado_planning"
CONFIG_DIR="/mnt/data/supervisor/homeassistant"
DATA_DIR="/mnt/data/supervisor/addons/data/fc4e2b3e_tado_planning"
CID_FILE="/mnt/data/supervisor/cid_files/addon_fc4e2b3e_tado_planning.cid"

VERSION=$(jq -r '.version' tado_planning/config.json)
IMAGE="fc4e2b3e/aarch64-addon-tado_planning:$VERSION"
TZ=$(printenv TZ || echo "Europe/Brussels")
SUPERVISOR_TOKEN=$(printenv SUPERVISOR_TOKEN || echo "")

# --- Stop and remove existing container --------------------------------------
echo "[DEPLOY] Stopping addon..."
ha apps stop "$ADDON_ID" 2>/dev/null || true
sleep 2
docker rm -f "$CONTAINER" 2>/dev/null || true

# --- Clean old images --------------------------------------------------------
echo "[DEPLOY] Cleaning old images..."
OLD_IMAGES=$(docker images --format '{{.Repository}}:{{.Tag}}' \
    | grep "fc4e2b3e/aarch64-addon-tado_planning" || true)
for img in $OLD_IMAGES; do
    docker rmi "$img" 2>/dev/null && echo "[DEPLOY] Removed $img" || true
done

# --- Build (repo root as context for cross-folder COPYs) ---------------------
echo "[DEPLOY] Building $IMAGE..."
docker build --no-cache -f tado_planning/Dockerfile -t "$IMAGE" "$REPO_DIR"
echo "[DEPLOY] Build OK"

# --- Ensure data dir and CID file exist --------------------------------------
mkdir -p "$DATA_DIR"
touch "$CID_FILE" 2>/dev/null || true

# --- Run with same mounts as HA supervisor -----------------------------------
echo "[DEPLOY] Starting container..."
docker run -d \
    --name "$CONTAINER" \
    --restart unless-stopped \
    -v /dev:/dev:ro \
    -v "${DATA_DIR}:/data" \
    -v "${CONFIG_DIR}:/config" \
    -v "${CID_FILE}:/run/cid:ro" \
    -p 8099:8099 \
    -e "TZ=${TZ}" \
    -e "SUPERVISOR_TOKEN=${SUPERVISOR_TOKEN}" \
    -e "HASSIO_TOKEN=${SUPERVISOR_TOKEN}" \
    "$IMAGE"

echo "[DEPLOY] Done — v$VERSION"
sleep 3
docker logs "$CONTAINER" | tail -20
