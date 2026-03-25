#!/usr/bin/env bash
# =============================================================================
# scripts/list_zones.sh — Liste les zones Tado configurées
#
# Fonctionne sur macOS et Home Assistant (SSH).
# Utilise le même token que tado_planning.py.
#
# Usage :
#   ./scripts/list_zones.sh
# =============================================================================

set -euo pipefail

# --- Couleurs ----------------------------------------------------------------
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

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
        TOKEN_FILE="/data/tado_refresh_token"
        PYTHON="python3"
        TADO_SCRIPT="/tado_planning.py"
        ;;
    mac)
        TOKEN_FILE="$PROJECT_DIR/tado_refresh_token"
        PYTHON=$(which python3.11 2>/dev/null || which python3)
        TADO_SCRIPT="$PROJECT_DIR/tado_planning.py"
        ;;
    linux)
        TOKEN_FILE="/data/tado_refresh_token"
        PYTHON="python3"
        TADO_SCRIPT="$PROJECT_DIR/tado_planning.py"
        ;;
esac

# --- En-tête -----------------------------------------------------------------
echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}║      tado-planning — Zones Tado          ║${RESET}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo ""

# --- Script Python inline ----------------------------------------------------
TADO_TOKEN_FILE="$TOKEN_FILE" \
$PYTHON - <<EOF
import os, sys
sys.path.insert(0, "$(dirname "$TADO_SCRIPT")")

TOKEN_FILE = os.environ.get("TADO_TOKEN_FILE", "$TOKEN_FILE")

try:
    from PyTado.interface.interface import Tado
    from PyTado.http import DeviceActivationStatus
    import webbrowser, time
except ImportError:
    print("[ERROR] python-tado not installed. Run: pip install 'python-tado>=0.18'")
    sys.exit(1)

print(f"[AUTH] Using token file: {TOKEN_FILE}")

tado = Tado(token_file_path=TOKEN_FILE)
status = tado.device_activation_status()

if status.value == "PENDING":
    url = tado.device_verification_url()
    print(f"\n[AUTH] First connection required.")
    print(f"[AUTH] Open this URL in your browser:\n")
    print(f"       {url}\n")
    try:
        webbrowser.open_new_tab(url)
    except Exception:
        pass
    print("[AUTH] Waiting for validation...")
    while True:
        try:
            tado.device_activation()
            break
        except Exception as e:
            print(f"[AUTH] Not yet validated, retrying in 10s...")
            time.sleep(10)
            tado = Tado(token_file_path=TOKEN_FILE)
elif status.value == "EXPIRED":
    from PyTado.http import TadoRequest, Action, Mode, Domain
    req = TadoRequest()
    req.command = "me"
    req.action  = Action.GET
    req.domain  = Domain.ME
    req.mode    = Mode.OBJECT
    me = tado._http.request(req)
    tado._http._id    = me["homes"][0]["id"]
    tado._http._x_api = False

print("[AUTH] Connected.\n")

me        = tado.get_me()
home_name = me["homes"][0]["name"]
print(f"Home : {home_name}\n")

zones = tado.get_zones()
print(f"{'Zone name':<30} {'ID':>5}   {'Type'}")
print("-" * 50)
for z in sorted(zones, key=lambda x: x["name"].lower()):
    print(f"  {z['name']:<28} {z['id']:>5}   {z.get('type', '?')}")

print(f"\n{len(zones)} zone(s) found.")
print()
print("Use zone names (lowercased, spaces replaced by _) in your weekconfig files.")
print("Example: 'Living Room' → 'living_room'")
print()
EOF
