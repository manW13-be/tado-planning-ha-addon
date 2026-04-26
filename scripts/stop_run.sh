#!/bin/bash

# =============================================================================
# scripts/stop_run.sh
# Arrête le processus ou le service tado_planning.
# =============================================================================

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# --- Détection de l'environnement --------------------------------------------
PLATFORM="unknown"
if [[ "$OSTYPE" == "darwin"* ]]; then
    PLATFORM="mac"
elif grep -q -i "hassio" /proc/1/cgroup 2>/dev/null; then
    PLATFORM="ha_docker"
else
    PLATFORM="ha_shell"
fi

echo "[INFO] Plateforme détectée : $PLATFORM"

# --- Arrêt du processus -----------------------------------------------------
case "$PLATFORM" in
    "mac")
        if pgrep -f "tado_planning/run.sh" > /dev/null; then
            echo "[KILL] Arrêt du processus shell..."
            pkill -f "tado_planning/run.sh"
        fi
        if launchctl list | grep -q "tado_planning"; then
            echo "[KILL] Arrêt du service launchd..."
            launchctl unload ~/Library/LaunchAgents/tado_planning.plist
        fi
        ;;
    "ha_docker")
        echo "[KILL] Arrêt du conteneur Docker..."
        docker stop tado_planning || true
        docker rm tado_planning || true
        ;;
    "ha_shell")
        if pgrep -f "tado_planning/run.sh" > /dev/null; then
            echo "[KILL] Arrêt du processus shell..."
            pkill -f "tado_planning/run.sh"
        fi
        ;;
    *)
        echo "[ERROR] Plateforme non supportée : $PLATFORM"
        exit 1
        ;;
esac

echo "[DONE] Processus arrêté."
exit 0