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

# node lives under ~/.nvm; non-interactive ssh doesn't source .profile, and
# /usr/local/bin/node symlinks into /root (unreadable by the app user), so
# PATH resolution fails with "permission denied". Prepend the newest
# installed version; the SPA build step below needs it.
NODE_BIN="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1 || true)"
if [ -n "$NODE_BIN" ]; then
    export PATH="$NODE_BIN:$PATH"
fi

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

# The SPA is built HERE (the VPS has node — the webapp-dist branch
# indirection is retired, spec §7): npm ci + vite build rewrite the single
# inlined web/static/index.html (vite-plugin-singlefile) over the committed
# snapshot. Any failure aborts before the migrations + restart, so the
# running service keeps the previous code and static file.
echo ">> Building SPA (web/frontend)"
if ! (cd web/frontend && npm ci --no-audit --no-fund && npm run build); then
    echo "!! SPA build failed — keeping previous deployment, no restart"
    exit 1
fi
if [ ! -s "$APP_DIR/web/static/index.html" ]; then
    echo "!! build produced no web/static/index.html — aborting"
    exit 1
fi
echo "Static built: $(sha256sum "$APP_DIR/web/static/index.html" | cut -c1-16)"

echo ">> Applying DB migrations (alembic)"
uv run --frozen --no-dev alembic upgrade head

echo ">> Restarting $SERVICE"
sudo -H systemctl restart "$SERVICE"
echo "Service state: $(sudo -H systemctl is-active "$SERVICE")"
echo "Deployed commit: $(git rev-parse --short HEAD)"
