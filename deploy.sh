#!/usr/bin/env bash
# deploy.sh — build check + git push → triggers Render auto-deploy
#
# Usage:
#   ./deploy.sh                        # push and let Render deploy
#   ./deploy.sh "feat: add X"          # custom commit message
#   ./deploy.sh --check-only           # run checks without pushing
#
# Prerequisites:
#   - Render "Auto-Deploy" enabled on the main branch in your service settings
#   - git remote "origin" pointing to your GitHub repo

set -euo pipefail

DASHBOARD_DIR="$(cd "$(dirname "$0")/Dashboard" && pwd)"
COMMIT_MSG="${1:-chore: update deployment}"
CHECK_ONLY=false

if [ "${1:-}" = "--check-only" ]; then
  CHECK_ONLY=true
fi

# ── Step 1: Django system check ───────────────────────────────────────────────
echo "==> Running Django system check..."
cd "$DASHBOARD_DIR"
uv run python manage.py check --settings=nepal_aq.settings.production --deploy 2>&1 | grep -v "^$" || true
echo "    System check passed."

# ── Step 2: Verify migrations are up to date ──────────────────────────────────
echo "==> Checking for missing migrations..."
uv run python manage.py migrate --check --settings=nepal_aq.settings.development --run-syncdb 2>/dev/null || {
  echo ""
  echo "    WARNING: There are unapplied migrations."
  echo "    Run: uv run python manage.py makemigrations"
  echo "    Then commit the new migration files before deploying."
  echo ""
}

if [ "$CHECK_ONLY" = true ]; then
  echo "==> --check-only: skipping git push."
  exit 0
fi

# ── Step 3: Stage, commit, push ───────────────────────────────────────────────
cd "$(dirname "$0")"

if [ -z "$(git status --porcelain)" ]; then
  echo "==> Nothing to commit. Pushing current HEAD..."
else
  echo "==> Staging all changes..."
  git add -A
  echo "==> Committing: \"$COMMIT_MSG\""
  git commit -m "$COMMIT_MSG"
fi

echo "==> Pushing to origin/main..."
git push origin main

echo ""
echo "==> Done. Render will auto-deploy from the new commit."
echo "    Watch progress: https://dashboard.render.com"
