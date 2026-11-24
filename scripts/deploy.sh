#!/usr/bin/env bash
# Deploy origin/<branch> on THIS machine (the "nl" VPS).
#
# Run as the app user (navid). Idempotent: pulls the target branch,
# syncs deps, applies migrations, restarts the systemd service.
# The systemd unit + .env are provisioned once (see docs/deployment.md);
# this script never touches .env or the database itself.
set -euo pipefail

# uv lives in ~/.local/bin; ssh non-interactive sessions don't get it on PATH
export PATH="$HOME/.local/bin:$PATH"

APP_DIR="/home/navid/projects/harzad_bot"
BRANCH="${DEPLOY_BRANCH:-main}"
SERVICE="harzad-bot"

cd "$APP_DIR"

echo ">> Pulling origin/$BRANCH"
git fetch origin "$BRANCH"
git checkout -f "$BRANCH"
git reset --hard "origin/$BRANCH"

echo ">> Syncing dependencies (uv)"
uv sync --frozen

echo ">> Applying DB migrations (alembic)"
uv run alembic upgrade head

echo ">> Restarting $SERVICE"
sudo -H systemctl restart "$SERVICE"
echo "Service state: $(sudo -H systemctl is-active "$SERVICE")"
echo "Deployed commit: $(git rev-parse --short HEAD)"
