#!/bin/bash
# =============================================================================
# scripts/docker_test_deploy.sh — Build the test Docker image
#
# Builds image 'addon_test_tado_planning' from local sources.
# Must be run from HA SSH in the repo directory.
# Refuses if prod container is running.
#
# Usage:
#   ./scripts/docker_test_deploy.sh
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROD_CONTAINER="addon_fc4e2b3e_tado_planning"
TEST_IMAGE="addon_test_tado_planning"
TEST_CONTAINER="addon_test_tado_planning"

log() { echo "[TEST-DEPLOY] $(date '+%d/%m/%Y %H:%M:%S') — $*"; }
die() { echo "[TEST-DEPLOY] ERROR: $*" >&2; exit 1; }

[ "$(uname)" = "Darwin" ] && die "This script must be run on HA SSH, not on macOS."

# Refuse if prod is running
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${PROD_CONTAINER}$"; then
    die "Prod container '$PROD_CONTAINER' is running.
  Stop the add-on from the HA UI first."
fi

# Remove stale test container if present
if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${TEST_CONTAINER}$"; then
    log "Removing stale test container '$TEST_CONTAINER'..."
    docker rm -f "$TEST_CONTAINER" >/dev/null
fi

# Remove old test image
if docker images --format '{{.Repository}}' 2>/dev/null | grep -q "^${TEST_IMAGE}$"; then
    log "Removing old test image '$TEST_IMAGE'..."
    docker rmi "$TEST_IMAGE" >/dev/null
fi

VERSION=$(jq -r '.version' "$REPO_DIR/tado_planning/config.json" 2>/dev/null || echo "unknown")
log "Building '$TEST_IMAGE' (v$VERSION) from $REPO_DIR..."

docker build --no-cache \
    -f "$REPO_DIR/tado_planning/Dockerfile" \
    -t "$TEST_IMAGE" \
    "$REPO_DIR"

log "Build complete."
log "Start with: ./scripts/docker_test_start.sh [--loop|--cfg|-vv|-d DATE|...]"
