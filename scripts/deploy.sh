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

# --no-dev everywhere (sync, alembic, systemd unit) so the venv state is
# identical on every deploy and every service start — no re-sync churn.
echo ">> Syncing dependencies (uv)"
uv sync --frozen --no-dev

# The VPS is node-free: the built SPA ships on the webapp-dist branch
# (GitHub Actions, .github/workflows/webapp.yml). Extract it and rsync it
# over the committed snapshot. Any failure here keeps the previous
# web/static/ intact — a broken build must never blank the app.
echo ">> Syncing built SPA (webapp-dist)"
TMP_STATIC="$(mktemp -d)"
trap 'rm -rf "$TMP_STATIC"' EXIT
if git fetch origin webapp-dist; then
    git archive origin/webapp-dist -- web/static | tar -x -C "$TMP_STATIC"
    if [ -s "$TMP_STATIC/web/static/index.html" ]; then
        rsync -a --delete "$TMP_STATIC/web/static/" "$APP_DIR/web/static/"
        echo "Static synced: $(sha256sum "$APP_DIR/web/static/index.html" | cut -c1-16)"
    else
        echo "!! webapp-dist has no web/static/index.html — keeping previous static dir"
    fi
else
    echo "!! webapp-dist branch unavailable — keeping previous static dir"
fi

echo ">> Applying DB migrations (alembic)"
uv run --frozen --no-dev alembic upgrade head

echo ">> Restarting $SERVICE"
sudo -H systemctl restart "$SERVICE"
echo "Service state: $(sudo -H systemctl is-active "$SERVICE")"
echo "Deployed commit: $(git rev-parse --short HEAD)"
