#!/usr/bin/env bash
# deploy.sh — one-command deploy to Render free tier
#
# Usage:  bash deploy.sh
#
# What it does:
#   1. Checks the repo is pushed to GitHub
#   2. Asks for your Render API key (create one at https://dashboard.render.com/u/settings#api-keys)
#   3. Triggers a Render Blueprint deploy using render.yaml
#   4. Polls until the web service is live and prints the URL
#
# Requirements: curl, git, jq  (all available on macOS by default except jq)
#   Install jq:  brew install jq

set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}▶ $*${RESET}"; }
success() { echo -e "${GREEN}✔ $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $*${RESET}"; }
die()     { echo -e "${RED}✘ $*${RESET}" >&2; exit 1; }

# ── 0. preflight checks ───────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Nepal Air Quality Dashboard — Render Deploy${RESET}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

command -v git  >/dev/null 2>&1 || die "git is not installed."
command -v curl >/dev/null 2>&1 || die "curl is not installed."
command -v jq   >/dev/null 2>&1 || die "jq is not installed. Run: brew install jq"

# ── 1. detect GitHub remote ───────────────────────────────────────────────────
info "Detecting GitHub remote..."

# Walk up from this script's directory to the git root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GIT_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)" \
    || die "Not inside a git repository. Run 'git init && git remote add origin <url>' first."

REMOTE_URL="$(git -C "$GIT_ROOT" remote get-url origin 2>/dev/null)" \
    || die "No git remote named 'origin'. Add one with: git remote add origin https://github.com/YOU/REPO"

# Normalise SSH → HTTPS so we can parse owner/repo
REMOTE_URL="${REMOTE_URL/git@github.com:/https://github.com/}"
REMOTE_URL="${REMOTE_URL%.git}"

if [[ "$REMOTE_URL" != *"github.com"* ]]; then
    die "Remote origin is not a GitHub URL. Render Blueprint requires GitHub.\nCurrent remote: $REMOTE_URL"
fi

GITHUB_REPO_PATH="${REMOTE_URL#*github.com/}"
GITHUB_OWNER="${GITHUB_REPO_PATH%%/*}"
GITHUB_REPO="${GITHUB_REPO_PATH##*/}"

success "GitHub repo: ${GITHUB_OWNER}/${GITHUB_REPO}"

# ── 2. check for uncommitted / unpushed changes ────────────────────────────────
info "Checking for unpushed commits..."

CURRENT_BRANCH="$(git -C "$GIT_ROOT" rev-parse --abbrev-ref HEAD)"
AHEAD="$(git -C "$GIT_ROOT" rev-list "origin/${CURRENT_BRANCH}...HEAD" 2>/dev/null | wc -l | tr -d ' ')"

if [[ "$AHEAD" -gt 0 ]]; then
    warn "You have ${AHEAD} unpushed commit(s) on branch '${CURRENT_BRANCH}'."
    warn "Render deploys from GitHub, so these won't be included."
    read -rp "Push now? [Y/n] " PUSH_CONFIRM
    if [[ "${PUSH_CONFIRM:-Y}" =~ ^[Yy]$ ]]; then
        git -C "$GIT_ROOT" push origin "$CURRENT_BRANCH"
        success "Pushed to origin/${CURRENT_BRANCH}"
    fi
fi

# ── 3. Render API key ──────────────────────────────────────────────────────────
echo ""
info "Render API key needed."
echo "  Get one at: ${BOLD}https://dashboard.render.com/u/settings#api-keys${RESET}"
echo "  (Create key → copy the value → paste below)"
echo ""

if [[ -n "${RENDER_API_KEY:-}" ]]; then
    success "Using RENDER_API_KEY from environment."
else
    read -rsp "  Paste your Render API key: " RENDER_API_KEY
    echo ""
fi

[[ -n "$RENDER_API_KEY" ]] || die "No API key provided."

# Validate the key
HTTP_CODE="$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${RENDER_API_KEY}" \
    "https://api.render.com/v1/owners?limit=1" 2>/dev/null)" || HTTP_CODE="000"

if [[ "$HTTP_CODE" != "200" ]]; then
    die "API key validation failed (HTTP $HTTP_CODE). Check the key and try again."
fi
success "API key valid."

# Get the owner ID (first workspace)
OWNER_ID="$(curl -sf \
    -H "Authorization: Bearer ${RENDER_API_KEY}" \
    "https://api.render.com/v1/owners?limit=1" \
    | jq -r '.[] | .owner.id' | head -1)"

[[ -n "$OWNER_ID" ]] || die "Could not determine Render owner ID."

# ── 4. Check if blueprint / services already exist ─────────────────────────────
info "Checking for existing services named 'nepal-aq-dashboard'..."

EXISTING="$(curl -sf \
    -H "Authorization: Bearer ${RENDER_API_KEY}" \
    "https://api.render.com/v1/services?limit=20&name=nepal-aq-dashboard" \
    | jq -r '.[] | .service.id' 2>/dev/null | head -1)" || EXISTING=""

if [[ -n "$EXISTING" ]]; then
    warn "Service 'nepal-aq-dashboard' already exists (id: $EXISTING)."
    warn "Triggering a manual re-deploy instead of creating a new blueprint."

    DEPLOY_RESP="$(curl -sf -X POST \
        -H "Authorization: Bearer ${RENDER_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"clearCache": false}' \
        "https://api.render.com/v1/services/${EXISTING}/deploys")"

    DEPLOY_ID="$(echo "$DEPLOY_RESP" | jq -r '.id')"
    success "Re-deploy triggered (deploy id: ${DEPLOY_ID})."
else
    # ── 5. Create blueprint from render.yaml via GitHub URL ──────────────────────
    info "Creating Render Blueprint from render.yaml..."

    BLUEPRINT_PAYLOAD="$(jq -n \
        --arg ownerId "$OWNER_ID" \
        --arg repoUrl "https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}" \
        --arg branch "$CURRENT_BRANCH" \
        '{
            ownerId: $ownerId,
            repoUrl: $repoUrl,
            branch:  $branch,
            autoSync: true
        }')"

    BLUEPRINT_RESP="$(curl -sf -X POST \
        -H "Authorization: Bearer ${RENDER_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$BLUEPRINT_PAYLOAD" \
        "https://api.render.com/v1/blueprints")" || BLUEPRINT_RESP=""

    if [[ -z "$BLUEPRINT_RESP" ]]; then
        warn "Blueprint API call returned empty — this usually means Render"
        warn "needs you to complete setup via the dashboard."
        echo ""
        echo -e "  ${BOLD}Open this URL to finish:${RESET}"
        echo -e "  ${CYAN}https://dashboard.render.com/blueprints/new?repo=${GITHUB_OWNER}/${GITHUB_REPO}&branch=${CURRENT_BRANCH}${RESET}"
        echo ""
        echo "  Render will read render.yaml and create all services automatically."
        exit 0
    fi

    BLUEPRINT_ID="$(echo "$BLUEPRINT_RESP" | jq -r '.blueprint.id // empty')"
    success "Blueprint created (id: ${BLUEPRINT_ID:-unknown})."
fi

# ── 6. Poll for the web service URL ───────────────────────────────────────────
echo ""
info "Waiting for the web service to become live (this takes ~3 minutes)..."

LIVE_URL=""
ATTEMPTS=0
MAX_ATTEMPTS=40   # 40 × 10s = ~6 minutes

while [[ -z "$LIVE_URL" && $ATTEMPTS -lt $MAX_ATTEMPTS ]]; do
    sleep 10
    ATTEMPTS=$((ATTEMPTS + 1))

    SERVICE_JSON="$(curl -sf \
        -H "Authorization: Bearer ${RENDER_API_KEY}" \
        "https://api.render.com/v1/services?limit=20&name=nepal-aq-dashboard" 2>/dev/null)" || continue

    STATUS="$(echo "$SERVICE_JSON" | jq -r '.[] | .service.suspended' 2>/dev/null | head -1)"
    URL="$(echo "$SERVICE_JSON"    | jq -r '.[] | .service.serviceDetails.url // empty' 2>/dev/null | head -1)"

    echo -ne "  attempt ${ATTEMPTS}/${MAX_ATTEMPTS} — status: ${STATUS:-unknown} ...\r"

    if [[ -n "$URL" && "$STATUS" != "suspended" ]]; then
        LIVE_URL="$URL"
    fi
done

echo ""   # clear the \r line

# ── 7. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ -n "$LIVE_URL" ]]; then
    success "Deploy complete!"
    echo ""
    echo -e "  Dashboard:  ${BOLD}${LIVE_URL}/dashboard/${RESET}"
    echo -e "  Django admin:  ${BOLD}${LIVE_URL}/django-admin/${RESET}  (admin / admin — change this!)"
    echo -e "  Wagtail CMS:   ${BOLD}${LIVE_URL}/cms-admin/${RESET}"
    echo -e "  REST API:      ${BOLD}${LIVE_URL}/api/v1/sensors/${RESET}"
    echo ""
    warn "Remember to seed dummy data if this is a fresh deploy:"
    echo "  → Render dashboard → nepal-aq-dashboard → Shell → python manage.py load_dummy_data"
else
    warn "Timed out waiting for the service URL."
    echo "  Check deploy status at: ${CYAN}https://dashboard.render.com${RESET}"
    echo "  The deploy may still be running — check back in a few minutes."
fi
echo ""
