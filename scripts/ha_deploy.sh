#!/bin/bash
# =============================================================================
# scripts/ha_deploy.sh — Rebuild Docker image(s) + redeploy add-on(s) (HA SSH)
#
# Bypasses 'ha apps restart' which always reuses the HA-cached image.
# Uses docker run directly with the same mounts as the HA supervisor.
#
# Usage:
#   ./scripts/ha_deploy.sh --run     # rebuild and redeploy tado-planning
#   ./scripts/ha_deploy.sh --cfg     # rebuild and redeploy tado-planning-cfg
#   ./scripts/ha_deploy.sh --all     # rebuild and redeploy both
#   ./scripts/ha_deploy.sh           # same as --all
# =============================================================================

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

CONFIG_DIR="/mnt/data/supervisor/homeassistant"
TZ=$(printenv TZ || echo "Europe/Brussels")
SUPERVISOR_TOKEN=$(printenv SUPERVISOR_TOKEN || echo "")

# ---------------------------------------------------------------------------
# Parse argument
# ---------------------------------------------------------------------------
TARGET="${1:---all}"
case "$TARGET" in
    --run|--cfg|--all) ;;
    *) echo "[DEPLOY] Unknown argument: $TARGET. Use --run, --cfg, or --all."; exit 1 ;;
esac

# ---------------------------------------------------------------------------
# Deploy function
# ---------------------------------------------------------------------------
deploy() {
    local ADDON="$1"

    if [ "$ADDON" = "run" ]; then
        local ADDON_ID="fc4e2b3e_tado_planning"
        local CONTAINER="addon_fc4e2b3e_tado_planning"
        local DATA_DIR="/mnt/data/supervisor/addons/data/fc4e2b3e_tado_planning"
        local CID_FILE="/mnt/data/supervisor/cid_files/addon_fc4e2b3e_tado_planning.cid"
        local DOCKERFILE="run/Dockerfile"
        local VERSION
        VERSION=$(jq -r '.version' run/config.json)
        local IMAGE="fc4e2b3e/aarch64-addon-tado_planning:$VERSION"
        local EXTRA_PORTS=""
    else
        local ADDON_ID="fc4e2b3e_tado_planning_cfg"
        local CONTAINER="addon_fc4e2b3e_tado_planning_cfg"
        local DATA_DIR="/mnt/data/supervisor/addons/data/fc4e2b3e_tado_planning_cfg"
        local CID_FILE="/mnt/data/supervisor/cid_files/addon_fc4e2b3e_tado_planning_cfg.cid"
        local DOCKERFILE="cfg/Dockerfile"
        local VERSION
        VERSION=$(jq -r '.version' cfg/config.json)
        local IMAGE="fc4e2b3e/aarch64-addon-tado_planning_cfg:$VERSION"
        local EXTRA_PORTS="-p 8099:8099"
    fi

    echo ""
    echo "[DEPLOY] ── $ADDON (v$VERSION) ──────────────────────────────"

    # Stop and remove existing container
    echo "[DEPLOY] Stopping..."
    ha apps stop "$ADDON_ID" 2>/dev/null || true
    sleep 2
    docker rm -f "$CONTAINER" 2>/dev/null || true

    # Clean old images
    echo "[DEPLOY] Cleaning old images..."
    OLD_IMAGES=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep "${IMAGE%:*}" || true)
    for img in $OLD_IMAGES; do
        docker rmi "$img" 2>/dev/null && echo "[DEPLOY] Removed $img" || true
    done

    # Build with repo root as context
    echo "[DEPLOY] Building $IMAGE..."
    docker build --no-cache -f "$DOCKERFILE" -t "$IMAGE" "$REPO_DIR"
    echo "[DEPLOY] Build OK"

    # Ensure data dir and CID file exist
    mkdir -p "$DATA_DIR"
    touch "$CID_FILE" 2>/dev/null || true

    # Run with same mounts as HA supervisor
    echo "[DEPLOY] Starting container..."
    docker run -d \
        --name "$CONTAINER" \
        --restart unless-stopped \
        -v /dev:/dev:ro \
        -v "${DATA_DIR}:/data" \
        -v "${CONFIG_DIR}:/config" \
        -v "${CID_FILE}:/run/cid:ro" \
        -e "TZ=${TZ}" \
        -e "SUPERVISOR_TOKEN=${SUPERVISOR_TOKEN}" \
        -e "HASSIO_TOKEN=${SUPERVISOR_TOKEN}" \
        $EXTRA_PORTS \
        "$IMAGE"

    echo "[DEPLOY] Done — v$VERSION"
    sleep 2
    docker logs "$CONTAINER" | tail -10
}

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------
case "$TARGET" in
    --run) deploy run ;;
    --cfg) deploy cfg ;;
    --all) deploy run; deploy cfg ;;
esac

echo ""
echo "[DEPLOY] All done."
