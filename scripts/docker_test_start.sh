#!/bin/bash
# =============================================================================
# scripts/docker_test_start.sh — Start the test Docker container
#
# Runs 'addon_test_tado_planning' with the same data mounts as prod.
# All arguments are forwarded to /run.sh inside the container.
#
# Modes:
#   (no flag)         single scheduler run — container exits after completion
#   --loop            scheduler loop + Flask — container stays alive (detached)
#   --cfg             Flask configurator — container stays alive (detached)
#   -vv / -d DATE / … passed through to the Python script
#
# Refuses if prod container is running.
#
# Usage:
#   ./scripts/docker_test_start.sh
#   ./scripts/docker_test_start.sh --loop
#   ./scripts/docker_test_start.sh --cfg
#   ./scripts/docker_test_start.sh -vv -d 2026-04-10
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROD_CONTAINER="addon_fc4e2b3e_tado_planning"
TEST_IMAGE="addon_test_tado_planning"
TEST_CONTAINER="addon_test_tado_planning"
CFG_PORT=8099

DATA_DIR="/mnt/data/supervisor/addons/data/fc4e2b3e_tado_planning"
CONFIG_DIR="/mnt/data/supervisor/homeassistant"

log() { echo "[TEST-START] $(date '+%d/%m/%Y %H:%M:%S') — $*"; }
die() { echo "[TEST-START] ERROR: $*" >&2; exit 1; }

[ "$(uname)" = "Darwin" ] && die "This script must be run on HA SSH, not on macOS."

# Refuse if prod is running
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${PROD_CONTAINER}$"; then
    die "Prod container '$PROD_CONTAINER' is running.
  Stop the add-on from the HA UI first."
fi

# Check test image exists
if ! docker images --format '{{.Repository}}' 2>/dev/null | grep -q "^${TEST_IMAGE}$"; then
    die "Test image '$TEST_IMAGE' not found.
  Build it first: ./scripts/docker_test_deploy.sh"
fi

# Remove stale test container if any
if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${TEST_CONTAINER}$"; then
    log "Removing stale test container..."
    docker rm -f "$TEST_CONTAINER" >/dev/null
fi

# Determine run mode from args
DETACHED=false
for arg in "$@"; do
    case "$arg" in
        --loop|--cfg) DETACHED=true ;;
    esac
done

TZ=$(printenv TZ 2>/dev/null || echo "Europe/Brussels")

log "Starting test container '$TEST_CONTAINER' — args: ${*:-<none>}"

if [ "$DETACHED" = true ]; then
    docker run -d \
        --name "$TEST_CONTAINER" \
        --hostname "addon-test-tado-planning" \
        -p "${CFG_PORT}:${CFG_PORT}" \
        -v "${DATA_DIR}:/data" \
        -v "${CONFIG_DIR}:/config" \
        -e "TZ=${TZ}" \
        "$TEST_IMAGE" \
        /run.sh "$@"

    log "Container started in background."
    log "Logs  : docker logs -f $TEST_CONTAINER"
    log "Stop  : ./scripts/docker_test_stop.sh"
    if [[ " $* " =~ " --cfg " ]] || [[ " $* " =~ " --loop " ]]; then
        log "Flask : http://homeassistant.local:${CFG_PORT}"
    fi
else
    # Single run — attached, container exits when done
    docker run --rm \
        --name "$TEST_CONTAINER" \
        --hostname "addon-test-tado-planning" \
        -v "${DATA_DIR}:/data" \
        -v "${CONFIG_DIR}:/config" \
        -e "TZ=${TZ}" \
        "$TEST_IMAGE" \
        /run.sh "$@"
fi
