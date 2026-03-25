#!/usr/bin/env bash
# =============================================================================
# install_launchd.sh — Installe et active le LaunchAgent pour tado-planning
# =============================================================================

set -euo pipefail

# --- Couleurs ----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

LABEL="com.tado-planning"
PLIST_NAME="${LABEL}.plist"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"

# --- Détection du répertoire projet ------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# --- Détection de Python -----------------------------------------------------
detect_python() {
    local candidates=(
        "/opt/homebrew/bin/python3.11"
        "/usr/local/bin/python3.11"
        "$(command -v python3.11 2>/dev/null || true)"
    )

    for candidate in "${candidates[@]}"; do
        if [[ -n "$candidate" && -x "$candidate" ]]; then
            echo "$candidate"
            return 0
        fi
    done

    return 1
}

check_python_version() {
    local python_bin="$1"
    local version
    version=$("$python_bin" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
    local major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)

    if [[ "$major" -lt 3 || ( "$major" -eq 3 && "$minor" -lt 10 ) ]]; then
        echo "${RED}✗ Python $version détecté — version 3.10 minimum requise.${RESET}"
        return 1
    fi
    echo "$version"
    return 0
}

# --- En-tête -----------------------------------------------------------------
echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}║      tado-planning — Installation macOS  ║${RESET}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo ""

# --- Détection Python --------------------------------------------------------
echo -e "${BOLD}🔍 Détection de Python...${RESET}"
if ! PYTHON_BIN=$(detect_python); then
    echo -e "${RED}✗ python3.11 introuvable.${RESET}"
    echo -e "  Installez-le via Homebrew : ${YELLOW}brew install python@3.11${RESET}"
    exit 1
fi

PYTHON_VERSION=$(check_python_version "$PYTHON_BIN") || exit 1
echo -e "  ${GREEN}✓ Python $PYTHON_VERSION${RESET} → ${PYTHON_BIN}"

# --- Chemins détectés --------------------------------------------------------
SCRIPT_PATH="${PROJECT_DIR}/tado_planning.py"
TOKEN_FILE="${PROJECT_DIR}/.tado_token"
SCHEDULES_DIR="${PROJECT_DIR}/schedules"
LOGS_DIR="${PROJECT_DIR}/logs"
LOG_OUT="${LOGS_DIR}/tado.log"
LOG_ERR="${LOGS_DIR}/tado_error.log"

echo ""
echo -e "${BOLD}📂 Configuration détectée :${RESET}"
echo -e "  Répertoire projet  : ${CYAN}${PROJECT_DIR}${RESET}"
echo -e "  Script Python      : ${CYAN}${SCRIPT_PATH}${RESET}"
echo -e "  Token file         : ${CYAN}${TOKEN_FILE}${RESET}"
echo -e "  Schedules dir      : ${CYAN}${SCHEDULES_DIR}${RESET}"
echo -e "  Log stdout         : ${CYAN}${LOG_OUT}${RESET}"
echo -e "  Log stderr         : ${CYAN}${LOG_ERR}${RESET}"
echo -e "  Plist destination  : ${CYAN}${PLIST_PATH}${RESET}"
echo -e "  Fréquence          : ${CYAN}toutes les heures (minute 0)${RESET}"

# --- Vérifications -----------------------------------------------------------
echo ""
WARNINGS=0

if [[ ! -f "$SCRIPT_PATH" ]]; then
    echo -e "${RED}✗ Script introuvable : ${SCRIPT_PATH}${RESET}"
    WARNINGS=$((WARNINGS + 1))
fi

if [[ ! -d "$SCHEDULES_DIR" ]]; then
    echo -e "${YELLOW}⚠ Dossier schedules absent, il sera créé : ${SCHEDULES_DIR}${RESET}"
fi

if [[ $WARNINGS -gt 0 ]]; then
    echo ""
    echo -e "${RED}${BOLD}Des erreurs bloquantes ont été détectées. Installation annulée.${RESET}"
    exit 1
fi

# --- Confirmation ------------------------------------------------------------
echo ""
echo -e "${YELLOW}${BOLD}Confirmer l'installation ? [o/N]${RESET} \c"
read -r confirm
if [[ ! "$confirm" =~ ^[oOyY]$ ]]; then
    echo -e "${YELLOW}Installation annulée.${RESET}"
    exit 0
fi

# --- Création des dossiers ---------------------------------------------------
mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$LOGS_DIR"
mkdir -p "$SCHEDULES_DIR"

# --- Génération du plist -----------------------------------------------------
echo ""
echo -e "${BOLD}📝 Génération du plist...${RESET}"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${SCRIPT_PATH}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>TADO_TOKEN_FILE</key>
        <string>${TOKEN_FILE}</string>
        <key>TADO_SCHEDULES_DIR</key>
        <string>${SCHEDULES_DIR}</string>
    </dict>

    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
    </array>

    <key>StandardOutPath</key>
    <string>${LOG_OUT}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_ERR}</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF

echo -e "  ${GREEN}✓ Plist créé${RESET}"

# --- Déchargement si déjà actif ----------------------------------------------
if launchctl list | grep -q "$LABEL" 2>/dev/null; then
    echo -e "${BOLD}🔄 Service déjà actif, déchargement...${RESET}"
    launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
fi

# --- Activation --------------------------------------------------------------
echo -e "${BOLD}🚀 Activation du LaunchAgent...${RESET}"
if launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"; then
    echo -e "  ${GREEN}✓ Service activé${RESET}"
else
    echo -e "${RED}✗ Échec de l'activation. Vérifiez le plist : ${PLIST_PATH}${RESET}"
    exit 1
fi

# --- Vérification finale -----------------------------------------------------
echo ""
if launchctl list | grep -q "$LABEL"; then
    echo -e "${GREEN}${BOLD}✅ tado-planning est installé et actif.${RESET}"
    echo -e "   Il s'exécutera toutes les heures."
    echo -e "   Logs : ${CYAN}${LOGS_DIR}/${RESET}"
else
    echo -e "${YELLOW}⚠ Le service a été chargé mais n'apparaît pas encore dans launchctl list.${RESET}"
    echo -e "  Attendez quelques secondes et vérifiez avec : ${CYAN}launchctl list | grep tado${RESET}"
fi

echo ""
