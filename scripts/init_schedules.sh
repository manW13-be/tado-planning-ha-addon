#!/usr/bin/env bash
# =============================================================================
# scripts/init_schedules.sh — Initialise schedules/ depuis schedules.tmpl/
#
# Fonctionne sur macOS et Home Assistant (SSH).
# Copie schedules.tmpl/ → schedules/ si schedules/ est absent ou vide.
# Si schedules/ existe déjà et n'est pas vide, demande confirmation.
#
# Usage :
#   ./scripts/init_schedules.sh           # init si absent
#   ./scripts/init_schedules.sh --force   # écrase même si existant
# =============================================================================

set -euo pipefail

# --- Couleurs ----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

FORCE=false
for arg in "$@"; do
    [[ "$arg" == "--force" ]] && FORCE=true
done

# --- Détection du contexte ---------------------------------------------------
if [ -f "/.dockerenv" ]; then
    CONTEXT="docker"
elif [ "$(uname)" = "Darwin" ]; then
    CONTEXT="mac"
else
    CONTEXT="linux"
fi

# --- Chemins selon contexte --------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

case "$CONTEXT" in
    docker)
        SCHEDULES_DIR="/config/tado-planning/schedules"
        SCHEDULES_TMPL="/schedules.tmpl"
        ;;
    mac|linux)
        SCHEDULES_DIR="$PROJECT_DIR/schedules"
        SCHEDULES_TMPL="$PROJECT_DIR/schedules.tmpl"
        ;;
esac

# --- En-tête -----------------------------------------------------------------
echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}║   tado-planning — Init schedules/        ║${RESET}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo ""

# --- Vérification du template ------------------------------------------------
if [ ! -d "$SCHEDULES_TMPL" ]; then
    echo -e "${RED}✗ Template not found : ${SCHEDULES_TMPL}${RESET}"
    echo -e "  Make sure schedules.tmpl/ exists in the project root."
    exit 1
fi

TMPL_COUNT=$(ls "$SCHEDULES_TMPL"/*.json 2>/dev/null | wc -l | tr -d ' ')
echo -e "  Template : ${CYAN}${SCHEDULES_TMPL}${RESET} (${TMPL_COUNT} files)"
echo -e "  Target   : ${CYAN}${SCHEDULES_DIR}${RESET}"
echo ""

# --- Vérification de la cible ------------------------------------------------
if [ -d "$SCHEDULES_DIR" ] && [ -n "$(ls -A "$SCHEDULES_DIR" 2>/dev/null)" ]; then
    EXISTING_COUNT=$(ls "$SCHEDULES_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ')

    if [ "$FORCE" = false ]; then
        echo -e "${YELLOW}⚠ schedules/ already exists with ${EXISTING_COUNT} file(s).${RESET}"
        echo ""
        echo -e "${YELLOW}${BOLD}Overwrite with template files ? [o/N]${RESET} \c"
        read -r confirm
        if [[ ! "$confirm" =~ ^[oOyY]$ ]]; then
            echo -e "${YELLOW}Cancelled — existing schedules/ unchanged.${RESET}"
            exit 0
        fi
    else
        echo -e "${YELLOW}--force : overwriting existing schedules/ (${EXISTING_COUNT} files).${RESET}"
    fi
fi

# --- Copie -------------------------------------------------------------------
echo ""
echo -e "${BOLD}📂 Copying template files...${RESET}"
mkdir -p "$SCHEDULES_DIR"
cp "$SCHEDULES_TMPL"/*.json "$SCHEDULES_DIR/"

COPIED=$(ls "$SCHEDULES_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ')
echo -e "  ${GREEN}✓ ${COPIED} file(s) copied to ${SCHEDULES_DIR}${RESET}"
echo ""
echo -e "${GREEN}${BOLD}✅ schedules/ initialized from template.${RESET}"
echo ""
echo -e "Next steps:"
echo -e "  1. ${CYAN}Edit planning_standard.json${RESET} — adapt days/times to your custody schedule"
echo -e "  2. ${CYAN}Edit kidspresent.json / kidsabsent.json${RESET} — set temperatures per zone"
echo -e "  3. ${CYAN}Run list_zones.sh${RESET} — check that zone names match your Tado setup"
echo -e "  4. ${CYAN}Run ./run.sh -vv${RESET} — verify everything works before enabling the scheduler"
echo ""
