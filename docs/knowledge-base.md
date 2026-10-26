# Harzad Bot — Knowledge Base

Date: 2026-10-26. Full extraction of project knowledge, taken **before the dependency
update** so every module's behavior can be re-verified after. Source of truth is the
code on `main` at this date (commit `5a9e44a`) plus live-verified runtime facts.

How to read: §1–§4 = what the code is, §5 = the box it runs on, §6 = per-feature flows,
§7 = Bot API landmines already paid for, §8–§9 = secrets + dead code, §10 = open issues,
§11 = test plan, §12 = workflow, §13 = dependency update plan (the actual next step).

---

## 1. What this project is

`@harzad_bot` — a Telegram media-downloader + AI bot (Python, python-telegram-bot 22.8,
uv-managed, Bot API 10.0).

Features (chat side, all via inline-keyboard navigation):

| Section | What it does | Engine |
|---|---|---|
| Instagram | posts, reels, stories, profile pics (post `/p/`, reel `/reel/`, story `/stories/` URLs or `@username`) | `instagrapi` (admin IG account, session JSON) |
| Music / Spotify | songs, albums, playlists → mp3 | `spotdl` (Spotify API client id/secret) |
| Pinterest | `https://pin.it/…` videos → mp4 | `yt-dlp` |
| YouTube | video → mp4 (≤720p, streaming, cover) or audio → mp3 192k; playlists loop; >50 MB → link + Copy button | `yt-dlp` |
| AI / Chat | free-form chat | local vLLM (`gemma4`) via `langchain_openai.ChatOpenAI` |
| AI / TTI | text→image 512×512 | together.ai SDXL (key in `.env`) |

Mini App (Phase B, shipped 2026-10-24): single-page app behind the chat menu button —
**Download tab** (same yt-dlp pipeline + `tg.downloadFile` "Save to device") and
**AI chat tab** (same vLLM). Runs on a free `localhost.run` SSH tunnel; watchdog keeps
it alive.

UI/UX roadmap (`docs/ui-ux-roadmap.md`): Phase C (polish), A (inline keyboards), B
(mini app) — **all three gates PASSED and merged to `main`**.

## 2. Repository layout

82 tracked files. Code paths that matter:

```
main.py                     Entry point: post_init, app builder, run_polling
configurations/settings.py  All env config (load_dotenv; required + optional vars)
bot/conversations/main.py   ConversationHandler wiring — 12 states
bot/navigation.py           navigate() — single inline-callback dispatcher (NAVIGATOR table)
bot/handlers/start.py       /start
bot/handlers/help.py        /help
bot/handlers/ai/{chat,tti,tti_replicate,before_chat,before_tti,main}.py
bot/handlers/youtube/{download,mp3,before_download,before_mp3,main}.py
bot/handlers/instagram/{download,before_download,main}.py
bot/handlers/music/spotify/{download,main}.py
bot/handlers/pinterest/{download,main}.py
connectors/db.py            SQLAlchemy engine/session from DB_URI (Postgres)
connectors/sqlite.py        DEAD (stdlib sqlite, imported nowhere)
connectors/spotdl.py        DEAD-ish (create_spotdl_instance imported in main, unused)
cruds/{user,log}.py         DB access: users + message logs
models/{user,log}.py        ORM models (tables: users, logs)
migrations/                 Alembic (2 migrations; legacy MySQL dialect types)
utils/constants/{states,keyboards,messages,__init__}.py
utils/keyboards.py          All InlineKeyboardMarkup instances (Phase A)
utils/helpers.py            escape_html/md, send_* helpers, react_message, prepare_cover, prepare_link
utils/decorators.py         send_action, sync_user, log_message, restricted
utils/instagram.py          IG client bootstrap (session file under login/)
utils/InstagramClient.py    instagrapi Client wrapper + exception→LoginException mapping
utils/Spotidl.py            DEAD (old spotdl fork shim; imports spotdl internals; imported nowhere)
utils/youtube_dl.py         Shared YDL_OPTS (mp4-only ≤720p + thumbnail + cookiefile)
utils/custom_context.py     MyContext.spotdl property (context_types wiring commented out in main)
utils/shared_handlers.py    /start + /help re-available in every state
utils/logger.py             root logger → logs/harzad_bot_<ts>.log + console
utils/exceptions/instagram.py  LoginException
web/app.py                  Mini App backend (aiohttp, embedded in bot loop)
web/static/index.html       Mini App SPA (vanilla JS, Telegram theme vars)
assets/logo.png             Bot avatar source (1254×1254 PNG → 512 JPEG at boot)
my_cookies.txt              yt-dlp cookies (gitignored, never committed)
donwload_test.py            SCRATCH SCRIPT — tracked, contains hardcoded Spotify creds (see §9)
makefile                    start/shell/sync/lock → uv
```

## 3. Architecture

### 3.1 Process model

One Python process = PTB `Application` (polling) **plus** an aiohttp server on
`127.0.0.1:8377` started in `post_init` via `web.app.start_server`. The web server
shares PTB's asyncio loop and reuses the same `ExtBot` for chat delivery.

Boot sequence (`main.py`):
1. `init_logger(logs/harzad_bot_<timestamp>.log)`
2. `create_requirement_folders()` (creates `./download` and `./login`)
3. Build `Spotdl(client_id, client_secret, downloader_settings)` →
   `application.bot_data["spotdl_instance"]`. Note `yt_dlp_args` here hardcodes
   `--cookies {YT_COOKIE_FILE} --impersonate chrome --force-ipv4 --proxy {YT_PROXY}`.
4. Builder: token, `post_init`, `connect_timeout(30)`, read/write/get-updates timeouts
   50s. (No persistence — `PicklePersistence` commented out.)
5. Handlers: `main_conversation_handler()` (group 0), `CallbackQueryHandler(answer_stray_callback)`
   (group 1), `on_error` (error handler).
6. `run_polling(bootstrap_retries=-1)` — retry forever through proxy outages.

`post_init` (runs in PTB's network-retry loop):
- `set_my_commands` (/start, /help), `set_my_description`, `set_my_short_description`
- `set_profile_photo`: `assets/logo.png` → 512×512 JPEG in memory →
  `setMyProfilePhoto(InputProfilePhotoStatic(InputFile(..., attach=True)))`.
  Failure never blocks boot.
- `start_mini_app`: always starts the web server (loopback-only); sets the
  **default** chat menu button (`MenuButtonWebApp(text="Open Harzad")`, no `chat_id`
  → default button) only when `MINIAPP_URL` is non-empty. Re-read from env every boot
  because the tunnel URL churns.

### 3.2 Conversation engine

- 12 integer states (`utils/constants/states.py`): START, INSTAGRAM, INSTAGRAM_DOWNLOAD,
  MUSIC, MUSIC_SPOTIFY, PINTEREST, YOUTUBE, YOUTUBE_DOWNLOAD, YOUTUBE_MP3, AI, AI_CHAT,
  AI_TTI = range(12).
- Entry: `/start` (also re-allowed in every state via `shared_handlers`, so the user
  can never get stranded).
- `navigate()` (`bot/navigation.py`): one `CallbackQueryHandler` first in every state.
  `NAVIGATOR: {cb_data: (text, keyboard, state)}`; always `cq.answer()`; edits the
  clicked message in place (`editMessageText`, HTML) — "message is not modified"
  swallowed; media messages that can't be re-texted → fresh menu message.
  Callback ids (`utils/constants/keyboards.py`): `nav:start`, `nav:ig`, `nav:ig:dl`,
  `nav:music`, `nav:music:spotify`, `nav:pin`, `nav:yt`, `nav:yt:dl`, `nav:yt:mp3`,
  `nav:ai`, `nav:ai:chat`, `nav:ai:ttI` (yes, the typo-case `ttI` is load-bearing).
- Leaf states keep a `filters.TEXT` MessageHandler for typed URLs / prompts.
- Group-1 `answer_stray_callback`: clicks before any conversation exists get answered
  (no hung spinner).
- `on_error`: logs traceback, 👎 reaction, "Something went wrong 🤯".
- Every handler is decorated `@send_action(ChatAction.TYPING)` `@sync_user`
  `@log_message` (TTI-replicate only has two). `sync_user` upserts the Telegram user
  row; `log_message` appends a row to `logs` with the raw text. **Both re-raise DB
  failures** → user sees the global error message if the DB is down (see §10 #1).

### 3.3 Feature handlers (chat)

**YouTube video** (`bot/handlers/youtube/download.py`):
`validators.url` gate → `YoutubeDL(YDL_OPTS)` (shared opts, §9-free) →
`extract_info` (DownloadError → 👎+error msg) → playlist? loop entries : single →
`ydl.download` + `process_info` → `prepare_filename` → moviepy `VideoFileClip` for
duration → `<50 MB`: `send_video(supports_streaming, cover+thumbnail from
prepare_cover, HTML caption w/ title, protect_content, state keyboard)` + 👍 :
`≥50 MB`: text with `<code>link</code>` + `CopyTextButton("📋 Copy link", data=link)`
(link = `prepare_link`: file path with `HOST_ROOT`→`HOSTNAME` substituted, spaces
`%20`-quoted — **HOSTNAME is `http://localhost/` today, dead from outside**, §10 #2).

**YouTube mp3** (`youtube/mp3.py`): own `ydl_opts` (bestaudio, `FFmpegExtractAudio`
mp3 192, restrictfilenames, cookiefile) → `AudioSegment` (pydub) for duration →
`send_audio(thumbnail=remote_url, caption=BOT_ID)`.

**Instagram** (`instagram/download.py`): `get_user_client_instagram()`
(`utils/instagram.py` → `InstagramClient.get_client`; session file
`login/{IG_USER}_{ADMIN_TG_ID}.json`; on LoginException: delete session, retry once,
else return None → error msg). URL branches: post/reel/story via `media_pk_from_url`
+ `media_info().model_dump()`; media_type constants (`utils/constants/__init__.py`:
photo=1, video/igtv/reel=2, album=8) × product_type (feed/igtv/clips) decide
photo/video send; album → loop `resources`; story → `story_pk_from_url`+`story_info`
(thumbnail always, video if present); bare username URL or `@username` →
`user_info_by_username` → profile pic (`.dict()` in the URL branch, `.model_dump()`
in the @ branch — inconsistent, both work on instagrapi 2.x). photo/video sends fall
back to `send_document` on any failure.

**Spotify** (`music/spotify/download.py`): `spotdl_instance` from
`context.application.bot_data` → `spotdl.search([url])` in a `ThreadPoolExecutor`
→ per-track `spotdl.download(song)` → `send_audio(title=s.json["name"],
performer=s.json["artist"], thumbnail=s.cover_url, duration)`. Missing
`p`/`cover_url` → silently skipped (AssertionError swallowed). End: "All done 🥳".

**Pinterest** (`pinterest/download.py`): own `ydl_opts` (mp4 best, no cookies, no
thumbnail) → extract/download/process → `send_video(thumbnail=remote url,
caption=BOT_ID)`. Non-`pin.it` URL → "should start with https://pin.it/".

**AI chat** (`ai/chat.py`): module-level `llm = ChatOpenAI(model=LLM_MODEL,
openai_api_base=LLM_BASE_URL, openai_api_key=LLM_API_KEY)` + `SYSTEM_PROMPT`
("You are Harzad…"). `await llm.ainvoke([("system",…), ("user", msg)])` → escaped
HTML reply. **These two module globals are also imported by `web/app.py:handle_chat`**
(keep the names stable).

**TTI** (`ai/tti.py`): `together.Image.create(prompt, model="stabilityai/
stable-diffusion-xl-base-1.0", 512×512, steps=40, seed=6616)` → b64decode →
`send_image`. Blocking SDK call (not in executor). Dead imports: `LLMChain`,
`PromptTemplate`, `ChatOpenAI` (unused). `ai/tti_replicate.py` = unused alternative
(replicate SDK, same dead imports).

### 3.4 Mini App (`web/app.py`, 380 lines)

Embedded aiohttp app; routes:

| Route | Auth | Behavior |
|---|---|---|
| `GET /` | — | serves `static/index.html` |
| `GET /api/health` | — | `{"ok": true}` |
| `POST /api/auth` | initData (or anon) | 401 if present-but-invalid; `{"user": null, "anonymous": true}` if absent; else id/first/last/username |
| `POST /api/jobs` | initData (or anon) | validate URL (`validators.url`); 409 if same user has an active job; uuid4 job; task `run_job` |
| `GET /api/jobs/{id}` | none (uuid = capability) | state/title/file_name/file_size_mb/file_url/file_id/error; 404 unknown |
| `GET /api/jobs/{id}/file` | none | Range-capable stream; `Content-Disposition: attachment` + `Access-Control-Allow-Origin: https://web.telegram.org` (required by `tg.downloadFile`) |
| `POST /api/chat` | initData (or anon) | last 10 messages, role-normalized (`bot/ai/assistant→assistant`, `user/human→human`), `llm.ainvoke` with SYSTEM_PROMPT → `{"reply": …}` |

Auth: `verify_init_data` — parse_qs (keep_blank), pop `hash`, check_string = all
remaining params sorted `k=v` newline-joined, `secret = HMAC-SHA256("WebAppData",
TOKEN)`, `HMAC(secret, check_string)` hexdigest compare. **Freshness from top-level
`auth_date` param** (current clients dropped the embedded one; embedded kept as
fallback); TTL 24 h. Verifier hashes *all* params except `hash` generically —
undocumented `signature` param from real clients matches without special-casing.
Mismatch logs param names + truncated raw blob (kept intentionally: the client blob
format has changed silently before).

Job lifecycle: `starting → extracting → sending → done | error`. `_ytdlp_run` in
`asyncio.to_thread` with the **shared `YDL_OPTS`**; playlists rejected in-app;
file recorded as `file_path` (real path from `prepare_filename` — outtmpl
string-concatenates, so `downloads/videos<Title>.mp4`); `file_url = {base}/api/jobs/
{id}/file`; chat delivery (authenticated, <50 MB) via the shared bot with
cover/thumbnail; jobs pruned 24 h after finish; `client_max_size` 1 MB.

SPA (`web/static/index.html`, 303 lines): vanilla JS, Telegram WebApp SDK, theme
variables (dark/light), every `tg.*` call guarded → "browser mode" banner outside
Telegram, Save falls back to `window.open(file_url)`. History roles `"bot"`/`"user"`;
chat payload `slice(0,-1)` drops the "…" placeholder turn.

### 3.5 Data layer

- `connectors/db.py`: engine + `SessionLocal` from `DB_URI` (psycopg2). `get_db()`
  generator (FastAPI-style; consumed by the decorators with `for db in get_db()`).
- Models: `users(id, tg_id, username, first_name, last_name, details JSON,
  last_login, created_on)`, `logs(id, tg_id, message TEXT, created_on)`. Both models
  use `sqlalchemy.dialects.mysql.INTEGER(11)` PKs (legacy from the seed project;
  harmless on Postgres). Alembic migrations (2024, MySQL dialect) match the old
  MySQL schema — **never run against the current DB**; schema is managed by hand.
- `sync_user`/`log_message` decorators are the only writers. Read API
  (`get_user`, `get_users`, …) exists but is unused.
- Current DB at test time: `harzad_test_pg` container, `127.0.0.1:5437/harzad`
  (users=1, logs=51 as of 2026-10-26 10:11).

### 3.6 Configuration

`configurations/settings.py` — `load_dotenv()` then `os.environ` reads.

| Var | Required | Default | Used by |
|---|---|---|---|
| `TOKEN` | yes | — | Bot, initData HMAC |
| `BOT_NAME` | yes | — | log file names |
| `SQLITE` | yes* | — | `connectors/sqlite.py` (dead — not imported) |
| `ADMINS` | yes* | — | `restricted` decorator (unused today) |
| `SPOTIFY_CLIENT_ID/SECRET` | yes | — | spotdl instance |
| `PROXY_LIST` | yes* | — | `next_proxy()` in IG error handling |
| `ADMIN_TELEGRAM_USER_ID` | yes | — | IG session file name |
| `INSTAGRAM_USER/PASSWORD` | yes | — | IG login |
| `DOWNLOAD_MP3_PATH` | yes | — | mp3 outtmpl (trailing slash expected) |
| `DOWNLOAD_VIDEO_PATH` | yes | — | video outtmpl (**no** trailing slash — string concat) |
| `HOSTNAME` / `HOST_ROOT` | yes | — | `prepare_link` (currently `http://localhost/`) |
| `SCRIPT_DIR` | yes | — | logo path |
| `LLM_BASE_URL/API_KEY/MODEL` | no | `http://127.0.0.1:40002/v1` / `not-needed` / `gemma4` | chat + mini app chat |
| `HF_TOKEN`, `REPLICATE_API_TOKEN`, `TOGETHER_API_KEY` | yes | — | (HF unused in code; replicate = dead path; together = TTI) |
| `DB_URI` | yes | — | SQLAlchemy |
| `YT_COOKIE_FILE`, `YT_PROXY` | yes | — | yt-dlp (chat + app + spotdl's yt_dlp_args) |
| `WEB_HOST`, `WEB_PORT` | no | `127.0.0.1`, `8377` | mini app server |
| `MINIAPP_URL` | no | `""` (disabled) | menu button + file_url base |

`*.example` drift (example is stale): has `OPENAI_API_KEY`, `OPENAI_MODEL`,
`USE_OLLAMA` (all dead — old chat backend), missing `LLM_BASE_URL/LLM_API_KEY/
LLM_MODEL` and `MINIAPP_URL`; `.env` sets `LANGCHAIN_TRACING_V2=false` (example
says true). `WEBHOOK`/`WEBHOOK_OPTIONS` constants exist but are unused (polling bot).

## 4. Dependencies

### 4.1 Direct deps — locked (2026-10-26) vs latest on PyPI

| Package | pyproject pin | Locked | Latest | Delta / risk |
|---|---|---|---|---|
| python-telegram-bot | `>=22,<23` | 22.8 | **22.8** | current |
| python-dotenv | `>=1.0,<2` | 1.2.4 | 1.2.4 | current |
| sqlalchemy | `>=2.0,<3` | 2.1.6 | 2.1.6 | current |
| alembic | `>=1.14,<2` | 1.20.1 | 1.20.1 | current |
| spotdl | `>=4.2,<5` | 4.5.2 | **4.6.0** | minor; internals `utils/Spotidl.py` imports may shift (module is dead) |
| validators | `>=0.34,<0.35` | 0.34.0 | **0.36.0** | pin blocks; API used: `validators.url` (stable) |
| pillow | `>=10.4,<11` | 10.4.0 | **13.0.0** | pin `<11` blocks; API used: open/convert/resize/thumbnail/save (stable across 10→13, but 11+ removed some legacy constants — `Image.LANCZOS` still fine as alias) |
| yt-dlp | `>=2024.12.23` | 2026.10.21 | **2026.10.21** | current |
| moviepy | `>=2.1.1,<3` | 2.2.1 | 2.2.1 | current |
| pydub | `==0.25.1` | 0.25.1 | 0.25.1 | frozen forever (stdlib `audioop` gone in 3.13) → **Python must stay 3.12** |
| langchain | `>=0.3.14,<0.4` | 0.3.30 | **1.4.7** | MAJOR. Only dead imports in this repo (`LLMChain`, `PromptTemplate` in tti.py/tti_replicate.py); chat uses `langchain_openai` |
| langchain-community | `>=0.3.14,<0.4` | 0.3.31 | 0.4.2 | MAJOR; imported by no module directly (transitive baggage) |
| langchain-core | `>=0.3.29,<0.4` | 0.3.86 | 1.7.0 | MAJOR; pinned by langchain-openai 0.2.14's constraints |
| langchain-openai | `>=0.2.14,<0.3` | 0.2.14 | **1.7.2** | MAJOR. `ChatOpenAI` API stable but kwarg renames happened (0.2→1.x: `openai_api_base`/`openai_api_key` → `base_url`/`api_key`; old names deprecated) |
| psycopg2-binary | `>=2.9.9,<3` | 2.9.13 | 2.9.13 | current |
| replicate | `>=0.23.1,<1` | 0.34.2 | **1.0.7** | MAJOR; used only by dead `tti_replicate.py` |
| together | `>=1.3,<2` | 1.3.14 | **3.0.0** | MAJOR; TTI feature (`together.Image.create` shape may differ) |
| instagrapi | `>=2.1,<3` | 2.1.5 | **3.2.1** | MAJOR; IG feature. 3.x: pydantic-v2 only, `.dict()` likely gone (code mixes `.dict()`/`.model_dump()`), Client kwarg/exception changes possible |
| requests | `>=2.32` | 2.34.2 | 2.34.2 | current |
| ffmpeg | `==1.4` | 1.4 | 1.4 | frozen (bundles ffmpeg binary for moviepy/pydub) |
| aiohttp | `>=3.14.5` | 3.14.5 | 3.14.5 | current |

### 4.2 Heavy transitive chains to watch

- **spotdl 4.x** pulls: fastapi, uvicorn, pymongo, redis, spotipy, spotipyfree,
  soundcloud-v2, syncedlyrics, ytmusicapi, yt-dlp, jaconv, pykakasi, tqdm, tabulate,
  jaconv… (big surface; `utils/Spotidl.py` imports spotdl *internals*
  — `spotdl._version`, `spotdl.console`, `spotdl.download.downloader`,
  `spotdl.types.*`, `spotdl.utils.*` — any internal rename breaks its import,
  though nothing imports it).
- **langchain 0.3 stack**: langsmith, tiktoken, pydantic-settings, openai 1.109.1,
  anyio 3.7.1 (old — pinned by langchain-core 0.3).
- **moviepy 2.x** → imageio, imageio-ffmpeg, numpy, proglog, decorator.
- **ptb 22.8** → httpx, httpcore, certifi, typing-extensions.

### 4.3 Python

`.python-version` = 3.12; `requires-python = ">=3.12,<3.13"` — the cap exists because
`pydub 0.25.1` imports stdlib `audioop` (removed in 3.13). .venv python = 3.12.8,
host pyenv also has 3.13.7 (used by the tunnel helper only). uv 0.5.9.

### 4.4 Dev group

pylint, black, isort, bandit, autopep8 — no test tooling at all (no pytest, no
conftest, zero test files). `pytest` latest = 9.1.1 (2026-10-26).

## 5. Runtime environment (the box)

Arch Linux container. Networking is the central constraint:

- **Direct egress** `46.34.170.229`: L7-blocked by Cloudflare → Bot API calls from
  plain shell fail; lhr.life reachable directly (heartbeat).
- **xray** at `127.0.0.1:10808` (mixed SOCKS5 + HTTP CONNECT) and `:20900`.
  Proxy egress `185.214.10.121` reaches Cloudflare + localhost.run.
- Bot process runs with `HTTPS_PROXY/HTTP_PROXY=http://127.0.0.1:10808`,
  `NO_PROXY=localhost,127.0.0.1`. Shell Bot API calls need `curl -x
  http://127.0.0.1:10808`.
- **vLLM** (AI backend): `http://127.0.0.1:40002/v1`, model `gemma4`
  (google/gemma-4-26B-A4B-it, 262k ctx), no auth, loopback-only, never proxied.
- **Tunnel**: ssh reverse to `nokey@localhost.run` (`-tt`, no remote command),
  ProxyCommand `python3 /tmp/opencode/ncx.py %h %p` (HTTP CONNECT through xray),
  `-R 80:localhost:8377`. localhost.run **re-issues the URL mid-session ~every 20 min**
  (old dies instantly: 502 "no tunnel"); sessions also drop.
- **Watchdog** `/tmp/opencode/tunnel_watchdog.sh` (recreated 2026-10-26 after the
  server restart wiped /tmp): auto-reconnect (15 s backoff), 15 s URL monitor
  (mid-session re-issue → `sed` `.env` `MINIAPP_URL` + bot restart), heartbeat on the
  newest URL, port-safe `start_bot` (pkill `python main[.]py`, wait ≤30 s, relaunch
  with env, poll `/api/health` ≤60 s). Logs: `/tmp/opencode/tunnel.log`,
  `/tmp/opencode/bot.log`.
- **Process layout**: watchdog + bot both launched `setsid nohup … & disown`
  (harness background shells get reaped; the 2026-10-26 OpenCode server restart reaped
  even the setsid group **and wiped /tmp** — watchdog/ncx had to be rebuilt from
  scratch; that's backlog item 1).
- **pkill footgun** (hit twice): always bracket the pattern
  (`pkill -f "python main[.]py"`); never combine pkill + relaunch in one command line
  — the relaunch line's own cmdline matches and kills the shell.
- **Docker** (don't touch the Supabase stack): `harzad_test_pg` = test Postgres
  `127.0.0.1:5437/harzad` (postgres/hz9x) — **was Exited, restarted 2026-10-26 10:11**.
  Local Supabase stack (`supabase_*_1819`, `baaham-*`), adminer, db/db_backup
  (mysql) = unrelated.
- Production Supabase DB reachable only in good proxy-egress windows — bot uses the
  local test DB instead.
- Downloads land in `downloads/videos*` / `downloads/mp3s*` (outtmpl string-concat,
  nothing auto-cleans; 108 MB as of 2026-10-26). IG sessions would land in `login/`
  (not present — none saved yet).
- User identity: tg_id `575570675` (@NabidaM; first_name is literally `:{` per Bot
  API). `ADMINS=1811946103`. Phone: Xiaomi 2311DRK48G, Android 16,
  Telegram-Android 13.1.0.

## 6. Per-feature data flows (verified)

1. **YouTube video (chat)**: text → PROCESSING msg → extract_info → DOWNLOADING msg →
   ydl.download+process_info → prepare_filename → moviepy duration → delete progress
   msg → send_video (cover+thumbnail, streaming) | link+Copy button → 👍/👎.
2. **Mini App download**: initData POST /api/jobs → uuid job → to_thread ytdlp →
   (auth & <50 MB) send_video to chat → state=done → SPA polls /api/jobs/{id} →
   "Save to device" via `tg.downloadFile(file_url)` → GET /file (Range, attachment,
   CORS web.telegram.org).
3. **Chat (both surfaces)**: message → (SPA: last-10 role-normalized) →
   `llm.ainvoke([system, …])` → HTML-escaped reply (chat) / JSON (app).
4. **IG**: session load (login/*.json) or login (2 attempts) → media pk from URL →
   info → type-based send (photo/video/album/story/pfp).
5. **Spotify**: search in executor → per-track download in executor → send_audio.
6. **Every handler**: `send_action` → `sync_user` (DB upsert) → `log_message`
   (DB insert) → logic.

## 7. Bot API knowledge already paid for (do not re-discover)

- PTB 22.8 = Bot API 10.0 support; 10.1–10.3 not in the library (audit:
  `docs/bot-api-feature-opportunities.md`).
- No `Message.react()` — use `bot.set_message_reaction` with `ReactionTypeEmoji`;
  only reaction-picker emojis accepted (✅/❌ rejected).
- `editMessageText` refuses reply keyboards (inline only) and "message is not
  modified" must be swallowed.
- `send_video` demotes webm to `document` → format string is mp4-only
  (`bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]`).
- YouTube thumbnails are `.webp` → `prepare_cover` re-encodes to ≤480 px JPEG
  (cover cap: 512 px / 200 KB; quality ladder 85/70/55, target ≤190 KB).
- `setMyProfilePhoto` needs `InputProfilePhotoStatic` + `InputFile(attach=True)`.
- `bootstrap_retries` is a `run_polling()` arg, not a builder method.
- `set_chat_menu_button` first positional is `chat_id` — omitted → **default**
  button (what we want; per-chat setting returns `{"type":"default"}` on query).
- Mini App origin lock (10.2, enforced since 2026-07-20): SPA + API single origin.
- `WebApp.downloadFile` needs an HTTPS URL (not file_id) + `Content-Disposition:
  attachment` + `Access-Control-Allow-Origin: https://web.telegram.org`.
- Real-client initData shape (Telegram-Android 13.1.0):
  `query_id, user=<json>, auth_date=<unix>, signature=<64>, hash=<64hex>`;
  `auth_date` top-level only; `user` has no auth_date; hash over all params except
  `hash`.
- Graceful PTB shutdown takes ~6–7 s → wait for process exit before rebinding :8377.
- `filters.TEXT` in a state shadows `/start` unless the CommandHandler is listed too
  (why `shared_handlers` exists in every state).
- yt-dlp resolves a bare-domain URL to an empty "recommended" playlist (hang risk,
  guarded now).

## 8. Secrets & sensitive files

- `.env` (gitignored): TOKEN, IG creds, Spotify creds, keys (HF/replicate/together),
  DB_URI, paths, MINIAPP_URL.
- `my_cookies.txt` (gitignored): Netscape cookie jar for yt-dlp.
- **`donwload_test.py` (TRACKED — committed) contains a hardcoded Spotify
  client id + secret** — rotate that secret or delete the file (cleanup candidate).
- `login/` (gitignored): would contain IG session JSONs (settings dump = auth cookies).
- `logs/` (gitignored except .gitignore): 50+ timestamped bot logs from 10-24..10-26.
- `assets/logo.png` tracked (public, fine).

## 9. Dead code & tech debt inventory

| Item | Status |
|---|---|
| `bot/handlers/*/before_*.py` (5 files) | dead since Phase A (replaced by `navigate`); deletion needs approval (backlog #3) |
| `utils/Spotidl.py` | dead; imports spotdl internals — breaks first on any spotdl internal rename |
| `connectors/sqlite.py` | dead (not imported; `SQLITE` env var exists only for it) |
| `connectors/spotdl.py:create_spotdl_instance` | imported in main.py, never called |
| `ai/tti.py`, `ai/tti_replicate.py` | dead imports (`LLMChain`, `PromptTemplate`, `ChatOpenAI`) — block langchain 1.x bump |
| `ai/tti_replicate.py` | entirely unused handler (replicate path) |
| `utils/custom_context.py` MyContext | wired nowhere (`.context_types()` commented in main) |
| `utils/decorators.py:restricted` | unused (ADMINS env exists for it) |
| `settings.py`: `WEBHOOK*`, `SQLITE` consumers, `HF_TOKEN` consumer | unused |
| `main.py`: `PicklePersistence`, `signal`, `sys`, `import_module` imports; commented `SpotifyClient.init` block | dead |
| `messages.py` lines 1–150 (rzashakeri seed texts) | mostly unused templates (lottery/insights/upload/feedback — features that don't exist) |
| `keyboards.py` constants `START/FIRST/SECOND/FINAL_KEYBOARD` | leftover from reply-keyboard era |
| `donwload_test.py` | scratch with hardcoded secrets |
| `.env.example` | stale (see §3.6) |
| `migrations/` | legacy MySQL-dialect; never run against current Postgres |
| `downloads/` clutter, 108 MB | no auto-cleanup anywhere |
| `videos/`, `download/` empty dirs | created by seed template / `create_requirement_folders` |
| `HOSTNAME=http://localhost/` | >50 MB fallback link is dead from outside (known, decision-logged) |

## 10. Open issues (current)

1. **DB availability**: `harzad_test_pg` died (33 h before 2026-10-26 10:11) while the
   bot pointed at it — every handler update would have failed at `sync_user` →
   "Something went wrong". Container restarted; no user-visible damage (no updates
   arrived in the gap). No liveness guard exists for the DB.
2. `>50 MB` fallback link dead (`HOSTNAME=http://localhost/`) — Copy button only.
3. Tunnel URL churn (~20 min) + client-side menu-button staleness → 502 window on
   phone tap; workaround = fresh URL message in chat. Stable domain = backlog #1.
4. Watchdog + ncx live in `/tmp` (wiped on server restart) → rebuild by hand =
   backlog #1.
5. TTI uses together.ai via a possibly-dead SDK path (together 1.3.14 vs 3.0 latest);
   unverified since the AI chat switch.
6. Instagram session not yet established (`login/` empty) — first IG use triggers
   login; untested this session.
7. Spotify download unverified this session (needs valid client creds + a track).
8. No tests anywhere; no CI.

## 11. Test plan (for the "one by one" phase)

Layering: pure unit → in-process integration (aiohttp test client, test DB) → live
smoke (real Telegram / real APIs). Proposed dev deps: `pytest`, `pytest-asyncio`,
maybe `respx` (httpx mocking for PTB-level unit tests).

| # | Module | Testable without network | Live part |
|---|---|---|---|
| 1 | `configurations.settings` | env-matrix loading, defaults | — |
| 2 | `utils.helpers` | escape_html/md, prepare_link, prepare_cover (fixture mp4+webp), stringify | — |
| 3 | `utils.keyboards` + constants | every keyboard has expected buttons/callbacks; CB set == NAVIGATOR keys | — |
| 4 | `bot.navigation` | NAVIGATOR shape; `navigate()` with mocked Update/CQ (edit path, not-modified path, media fallback path) | — |
| 5 | `web.app.verify_init_data` | auth matrix: valid/forged/no-hash/stale/missing user/`signature`-param blob (fixture from real phone) | — |
| 6 | `web.app` job lifecycle | mocked `_ytdlp_run` (fake file) → states, 409 double-job, pruning, file Range matrix (full/suffix/partial/416) | — |
| 7 | `web.app.handle_chat` | role normalization table; empty history 400 | vLLM round-trip |
| 8 | `web.app` endpoints | anon vs auth matrix on all routes via `aiohttp.test_utils.TestClient` | — |
| 9 | `connectors.db` + cruds | create/update/get user+log on `harzad_test_pg` (fresh schema or temp tables) | — |
| 10 | `utils.decorators` | sync_user/log_message with real test DB; failure re-raise | — |
| 11 | `youtube_dl.YDL_OPTS` | opts shape | real yt-dlp single video (proxy) |
| 12 | chat handlers × 6 | handler-level with mocked bot (PTB `TestBot`/httpx mock) | live per-feature: YT video, YT mp3, Spotify, IG (session), Pinterest, AI chat, TTI |
| 13 | `main.py` bootstrap | post_init order; menu-button only-when-URL logic | real boot (already routine) |
| 14 | `utils.instagram`/`InstagramClient` | exception→LoginException mapping | IG login + one media (needs creds/session) |

## 12. Git & workflow

- `main` (pushed) = `5a9e44a` (backlog docs). Phase merges: C `162e2d1`/`98ad365`,
  A `cd2b35f`, B `f5d1ffb`. Stale local branches: `mini-app`, `inline-keyboards`,
  `ux-polish`, `uv-migration` (all merged); remote extras: `clean`, `develop`.
- Phase workflow (user-fixed): implement on branch → **live gate** → user confirms →
  commit → merge `--no-ff` to main → push. Deps + test phase follows the same
  confirm-first rule per the user's 2026-10-26 instruction.
- Commits: normal prose (caveman mode is conversation-only).
- `uv` project: `package = false` (app repo, deps only).

## 13. Dependency update plan (proposed — awaiting go-ahead)

Ordered lowest-risk first; after each step: `uv lock` + import smoke + the matching
test from §11.

1. **Dev group**: add `pytest`, `pytest-asyncio` (test tooling; zero app impact).
2. **Bumps that are already at latest / no-op**: PTB, dotenv, sqlalchemy, alembic,
   yt-dlp, moviepy, psycopg2, requests, aiohttp, ffmpeg, pydub — verify lock only.
3. **validators 0.34 → 0.36** (un-pin `<0.35`): one API in use (`validators.url`).
4. **spotdl 4.5.2 → 4.6.0**: minor; re-import `utils/Spotidl.py` (dead but importable
   should stay importable) — or delete it (needs approval, backlog-adjacent).
5. **pillow 10.4 → 11/12/13**: un-pin `<11`; exercise `prepare_cover` + avatar resize.
6. **langchain stack 0.3 → 1.x**: requires deleting the dead
   `LLMChain`/`PromptTemplate`/`ChatOpenAI`-unused imports in `tti.py` /
   `tti_replicate.py` first; update `chat.py` kwargs to 1.x names
   (`base_url`, `api_key`) if the old aliases warn; `langchain-community` can likely
   drop entirely (nothing imports it directly). Verify chat still answers.
7. **together 1.3 → 3.0**: verify `together.Image.create` response shape against live
   key (TTI feature). If 3.0 changed the API, either adapt or pin 1.3 and note it.
8. **instagrapi 2.1 → 3.x**: biggest feature risk. Migrate `.dict()` →
   `.model_dump()` (1 site), check `Client(request_timeout=)` + exception names +
   `delay_range`/`set_proxy` against 3.x, then live-test one media with the admin
   session. If 3.x login flow breaks, pin 2.1.5 and note it.
9. **replicate 0.34 → 1.0.7**: only the dead path imports it — delete
   `tti_replicate.py` (needs approval) or bump + import-check.
10. Final: full boot + mini-app smoke (health, auth, one chat, one download) and
    §11 live pass.

Standing constraints: Python stays 3.12 (pydub/audioop); `ffmpeg==1.4` stays pinned;
vLLM is loopback-only and never behind the proxy; `YT_PROXY` stays
`http://127.0.0.1:10808`.
