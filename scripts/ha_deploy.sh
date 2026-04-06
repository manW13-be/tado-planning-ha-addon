#!/bin/bash
# =============================================================================
# scripts/ha_deploy.sh — Rebuild Docker image + redeploy add-on (HA SSH)
#
# Uses docker run directly instead of 'ha apps restart' to ensure the
# freshly built image is used (HA supervisor ignores local images otherwise).
#
# Usage:
#   ./scripts/ha_deploy.sh
# =============================================================================

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ADDON_ID="fc4e2b3e_tado_planning"
ADDON_CONTAINER="addon_fc4e2b3e_tado_planning"
DATA_DIR="/mnt/data/supervisor/addons/data/fc4e2b3e_tado_planning"
CONFIG_DIR="/mnt/data/supervisor/homeassistant"
CID_FILE="/mnt/data/supervisor/cid_files/addon_fc4e2b3e_tado_planning.cid"
cd "$REPO_DIR"

VERSION=$(jq -r '.version' run/config.json)
IMAGE="fc4e2b3e/aarch64-addon-tado_planning:$VERSION"

# --- Stop and remove existing container --------------------------------------
echo "[DEPLOY] Stopping add-on..."
ha apps stop "$ADDON_ID" 2>/dev/null || true
sleep 2
docker rm -f "$ADDON_CONTAINER" 2>/dev/null || true

# --- Clean old images --------------------------------------------------------
echo "[DEPLOY] Cleaning old images..."
OLD_IMAGES=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep "fc4e2b3e/aarch64-addon-tado_planning" || true)
for img in $OLD_IMAGES; do
    docker rmi "$img" 2>/dev/null && echo "[DEPLOY] Removed $img" || true
done

# --- Build -------------------------------------------------------------------
echo "[DEPLOY] Building image $IMAGE..."
docker build --no-cache -f run/Dockerfile -t "$IMAGE" "$REPO_DIR"
echo "[DEPLOY] Build OK"

# --- Collect env from supervisor token ---------------------------------------
SUPERVISOR_TOKEN=$(printenv SUPERVISOR_TOKEN || echo "")
TZ=$(printenv TZ || echo "Europe/Brussels")

# --- Run container with same mounts as HA supervisor -------------------------
echo "[DEPLOY] Starting container from local image..."
docker run -d \
    --name "$ADDON_CONTAINER" \
    --restart unless-stopped \
    -v /dev:/dev:ro \
    -v "${DATA_DIR}:/data" \
    -v "${CONFIG_DIR}:/config" \
    -v "${CID_FILE}:/run/cid:ro" \
    -e "TZ=${TZ}" \
    -e "SUPERVISOR_TOKEN=${SUPERVISOR_TOKEN}" \
    -e "HASSIO_TOKEN=${SUPERVISOR_TOKEN}" \
    "$IMAGE"

echo "[DEPLOY] Done — v$VERSION — waiting for logs..."
sleep 3
docker logs "$ADDON_CONTAINER" | tail -20
