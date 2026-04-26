#!/bin/bash

# =============================================================================
# scripts/check_run.sh
# Vérifie si une instance de l'addon tado_planning est en cours d'exécution.
# Compatible : HA (shell/docker), Mac (shell)
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

# --- Vérification du processus -----------------------------------------------
RUNNING=false

case "$PLATFORM" in
    "mac"|"ha_shell")
        # Mode direct (shell)
        if pgrep -f "tado_planning/run.sh" > /dev/null; then
            echo "[INFO] Une instance shell de tado_planning est en cours (PID: $(pgrep -f "tado_planning/run.sh"))."
            RUNNING=true
        fi
        ;;
    "ha_docker")
        # Mode Docker (HA)
        if docker ps | grep -q "tado_planning"; then
            echo "[INFO] Une instance Docker de tado_planning est en cours."
            RUNNING=true
        fi
        ;;
    *)
        echo "[ERROR] Plateforme non supportée : $PLATFORM"
        exit 1
        ;;
esac

if [ "$RUNNING" = false ]; then
    echo "[INFO] Aucune instance de tado_planning n'est en cours d'exécution."
fi

exit 0