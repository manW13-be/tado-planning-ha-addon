#!/bin/bash
# =============================================================================
# scripts/fetch.sh — Pull depuis GitHub (universel Mac + HA SSH)
#
# Usage :
#   ./scripts/fetch.sh
# =============================================================================

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo "[FETCH] Pulling from GitHub..."
git pull origin main

# Synchro .gitignore → gitignore (visible depuis Finder/Samba)
if [ -f ".gitignore" ]; then
    cp .gitignore gitignore
    echo "[FETCH] .gitignore → gitignore"
fi

echo "[FETCH] Done — v$(jq -r '.version' config.json)"
