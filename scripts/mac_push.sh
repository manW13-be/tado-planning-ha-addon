#!/bin/bash
# =============================================================================
# scripts/mac_push.sh — Bump version + commit + push GitHub
# Usage: ./scripts/mac_push.sh "message de commit"
# =============================================================================

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# Copier gitignore → .gitignore
if [ -f "gitignore" ]; then
    cp gitignore .gitignore
    echo "[PUSH] gitignore → .gitignore"
fi

# Fetch la version courante depuis GitHub avant de bumper
echo "[PUSH] Fetching latest version from GitHub..."
git fetch origin main
REMOTE_VERSION=$(git show origin/main:config.json | jq -r '.version')
IFS='.' read -r MAJOR MINOR PATCH <<< "$REMOTE_VERSION"
NEW_VERSION="$MAJOR.$MINOR.$((PATCH + 1))"
jq --arg v "$NEW_VERSION" '.version = $v' config.json > config.json.tmp && mv config.json.tmp config.json

echo "[PUSH] Version: $REMOTE_VERSION → $NEW_VERSION"

git add -A
git commit -m "${1:-update v$NEW_VERSION}"
git push origin main

echo "[PUSH] Done — v$NEW_VERSION pushed to GitHub"
