#!/bin/bash
# =============================================================================
# scripts/push.sh — Commit + push GitHub (universel Mac + HA SSH)
#
# Usage :
#   ./scripts/push.sh "message"          # commit + push, version inchangée
#   ./scripts/push.sh --bump "message"   # bump patch version + commit + push
#
# Pourquoi --bump fetch la version remote ?
#   Le numéro de version dans config.json est utilisé par le store HA pour
#   détecter les mises à jour. Pour éviter les conflits si plusieurs machines
#   pushent, la version est toujours lue depuis GitHub avant d'être incrémentée.
# =============================================================================

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# --- Parsing des arguments ---------------------------------------------------
BUMP=false
COMMIT_MSG=""

for arg in "$@"; do
    case "$arg" in
        --bump) BUMP=true ;;
        *)      COMMIT_MSG="$arg" ;;
    esac
done

# --- Synchro gitignore → .gitignore -----------------------------------------
if [ -f "gitignore" ]; then
    cp gitignore .gitignore
    echo "[PUSH] gitignore → .gitignore"
fi

# --- Bump de version (optionnel) --------------------------------------------
if [ "$BUMP" = true ]; then
    echo "[PUSH] Fetching latest version from GitHub..."
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    git fetch origin "$BRANCH"
    REMOTE_VERSION=$(git show origin/"$BRANCH":tado_planning/config.json | jq -r '.version')
    IFS='.' read -r MAJOR MINOR PATCH <<< "$REMOTE_VERSION"
    NEW_VERSION="$MAJOR.$MINOR.$((PATCH + 1))"
    jq --arg v "$NEW_VERSION" '.version = $v' tado_planning/config.json > tado_planning/config.json.tmp && mv tado_planning/config.json.tmp tado_planning/config.json
    echo "[PUSH] Version: $REMOTE_VERSION → $NEW_VERSION"
    CURRENT_VERSION="$NEW_VERSION"
else
    CURRENT_VERSION=$(jq -r '.version' tado_planning/config.json)
    echo "[PUSH] Version unchanged: $CURRENT_VERSION"
fi

# --- Commit et push ----------------------------------------------------------
FINAL_MSG="${COMMIT_MSG:-update v$CURRENT_VERSION}"
git add -A
git commit -m "$FINAL_MSG"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git push origin "$BRANCH"

echo "[PUSH] Done — v$CURRENT_VERSION pushed to GitHub"
