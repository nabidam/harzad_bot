# Deployment

Production host: **nl** (`185.214.10.121`, Ubuntu 24.04, user `navid`, root for system changes).
Deploy target: `main` branch — GitHub Actions (`.github/workflows/deploy.yml`) runs the test
suite, then SSHes to the box and runs `scripts/deploy.sh`.

## Topology

| Component | Where | Notes |
|---|---|---|
| Bot (PTB polling + embedded mini-app web server) | systemd `harzad-bot`, user `navid`, `uv run` | `127.0.0.1:8377` for the web server |
| Postgres 18 | docker container `harzad-pg`, volume `harzad_pg_data` | `127.0.0.1:5432`, db `harzad_db`, user `baaham` |
| Mini App HTTPS | nginx vhost `harzad.navidam.ir` → `127.0.0.1:8377` | cert in `/etc/letsencrypt/live/harzad.navidam.ir` |
| `.env` | `/home/navid/projects/harzad_bot/.env` (gitignored) | provisioned once, **never touched by deploys** |

The VPS has direct egress — `TG_PROXY`/`YT_PROXY` are empty there (the dev box L7-blocks
direct egress and uses xray on `127.0.0.1:10808`).

The AI chat's `LLM_BASE_URL` points at the dev box's local LLM and is unreachable from the
VPS — AI chat tasks fail gracefully until it is pointed at a real OpenAI-compatible endpoint.

## One-time setup (already done on 2026-11-24)

1. `uv` installed for `navid` (`~/.local/bin/uv`).
2. Postgres container:
   `docker run -d --name harzad-pg --restart unless-stopped -e POSTGRES_USER=baaham \
   -e POSTGRES_PASSWORD=... -e POSTGRES_DB=harzad_db -v harzad_pg_data:/var/lib/postgresql/data \
   -p 127.0.0.1:5432:5432 postgres:18`
3. Initial data migrated from the dev box via `pg_dump -Fc` → `pg_restore`.
4. `/etc/sudoers.d/harzad-bot`: `navid` may `systemctl restart harzad-bot` / `is-active`
   (passwordless, nothing else).
5. `/etc/systemd/system/harzad-bot.service` (enabled; `Restart=always`):
   `ExecStart=/home/navid/.local/bin/uv run --frozen --no-dev python main.py`
6. nginx vhost: `deploy/nginx/harzad.navidam.ir.conf` → `/etc/nginx/sites-available/` +
   symlink in `sites-enabled`, `nginx -t && systemctl reload nginx`.
7. SSH: repo deploy key `gh-actions-nl` (read-only) → `navid`'s `authorized_keys`;
   GitHub secrets `NL_DEPLOY_KEY` (private key) and `NL_HOST_KEY` (host key).

## Routine operations

```bash
# deploy is automatic on push to main; manually:
ssh navid@185.214.10.121 'bash ~/projects/harzad_bot/scripts/deploy.sh'

# logs
ssh root@185.214.10.121 'journalctl -u harzad-bot -f'

# DB
ssh navid@185.214.10.121 'docker exec -it harzad-pg psql -U baaham -d harzad_db'

# backup
ssh root@185.214.10.121 'docker exec harzad-pg pg_dump -U baaham harzad_db -Fc' > harzad_db.dump
```

New release branch / host: create the systemd unit + sudoers + nginx vhost there first,
then push. Deploys never create the `.env` — copy one and adjust paths.
