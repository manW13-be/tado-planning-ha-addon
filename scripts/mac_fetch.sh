#!/bin/bash
# =============================================================================
# scripts/mac_fetch.sh — Pull depuis GitHub + .gitignore → gitignore
# Usage: ./scripts/mac_fetch.sh
# =============================================================================

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo "[FETCH] Pulling from GitHub..."
git pull origin main

# Copier .gitignore → gitignore (éditable depuis Finder)
if [ -f ".gitignore" ]; then
    cp .gitignore gitignore
    echo "[FETCH] .gitignore → gitignore"
fi

echo "[FETCH] Done — v$(jq -r '.version' config.json)"
