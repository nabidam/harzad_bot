# Harzad Bot — Knowledge Base

Date: 2026-10-26. Full extraction of project knowledge, taken **before the dependency
update** so every module's behavior can be re-verified after. Source of truth is the
code on `main` at this date (commit `5a9e44a`) plus live-verified runtime facts.

**Update 2026-10-26 (commit `c398813`):** TTI image generation (together/replicate)
disabled per user decision — chat kept. Low-risk bumps applied: validators 0.36,
pillow 11.3, spotdl 4.6.0; `together`/`replicate` deps dropped; pytest +
pytest-asyncio added to dev group. Verified (imports 37/37, behavior checks,
live chat round-trip). §4/§9/§13 below reflect this where marked ✅.

**Update 2026-10-26, round 2 (commit `f4d4586`):** majors applied — langchain
0.3 → 1.4.6 / core 1.6.10 / openai-integration 1.7.2 (openai SDK 3.37),
langchain-community dropped, instagrapi 2.1 → 3.2.1. **moviepy dropped**
(hard pillow conflict with instagrapi 3.x; it was only ever used for reading
video duration → now `mutagen`); pillow 13.0.0. Breakage found and fixed:
only the resolver conflict + dead `LLMChain`/`PromptTemplate` imports.
Live IG login blocked on empty `INSTAGRAM_USER`/`INSTAGRAM_PASSWORD` in `.env`
(pre-existing gap — fails identically on 2.1.5).

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
| AI / TTI | ~~text→image 512×512~~ | **DISABLED 2026-10-26** (together.ai SDXL; button removed, handlers kept with lazy SDK imports) |

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
2. **Single-instance lock**: non-blocking `flock` on `run/harzad_bot.lock`
   (gitignored); on contention it logs the holder pid and `sys.exit(1)`.
   Two processes polling one token = 409 Conflict storm + duplicate update
   delivery (two fresh pollers' in-flight long-polls with overlapping offsets
   can both receive the same update). Added after the 2026-10-27
   double-listener incident. The fd is held in a module-level `_BOT_LOCK_FD`
   for the process lifetime (closing the fd drops the lock).
3. `create_requirement_folders()` (creates `./download` and `./login`)
4. Build `Spotdl(client_id, client_secret, downloader_settings)` →
   `application.bot_data["spotdl_instance"]`. Note `yt_dlp_args` here hardcodes
   `--cookies {YT_COOKIE_FILE} --impersonate chrome --force-ipv4 --proxy {YT_PROXY}`.
   Immediately after: `asyncio.set_event_loop(asyncio.new_event_loop())` —
   spotdl's `Downloader.__init__` already did `set_event_loop(its own fresh
   loop)` in the main thread, and PTB's `Application.__run` picks the main
   thread's loop up via `get_event_loop()` and runs the whole bot on it —
   that is what broke spotdl's sync download API (2026-10-27, §10.14). The
   reset keeps the bot loop and spotdl's loop separate objects.
5. Builder: token, `post_init`, `connect_timeout(30)`, read/write/get-updates timeouts
   50s. (No persistence — `PicklePersistence` commented out.)
6. Handlers: `main_conversation_handler()` (group 0), `CallbackQueryHandler(answer_stray_callback)`
   (group 1), `on_error` (error handler; distinguishes `update=None`
   polling-layer errors — e.g. 409 Conflict — from handler errors).
7. `run_polling(bootstrap_retries=-1)` — retry forever through proxy outages.

`post_init` — **NOT** in PTB's network-retry loop (only the bootstrap
`initialize` is wrapped, per `Application.__run`):
- `set_my_commands` (/start, /help), `set_my_description`,
  `set_my_short_description` — wrapped in one try/except (2026-10-27 10:49:
  a `setMyShortDescription` 50 s read timeout via the flaky proxy propagated
  out of `post_init` and killed the process 50 s after start; the old
  docstring claiming retry coverage was wrong). Failure only logs a
  warning — profile cosmetics are re-set on the next boot.
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
  AI_TTI = range(12). The conversation wires **11** — `AI_TTI_STATE` still exists in
  `states.py` (imported by the dormant `tti.py`) but has no handlers since TTI was
  disabled on 2026-10-26.
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
`ydl.download` + `process_info` → `prepare_filename` → **mutagen for duration**
(moviepy was only ever used for this and was dropped 2026-10-26 — see §13) → `<50 MB`: `send_video(supports_streaming, cover+thumbnail from
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
(pure sync HTTP — no event loop involved) → per-track
`await spotdl.downloader.pool_download(song)` — spotdl's async per-song unit,
awaited directly on the running bot loop (never the sync `spotdl.download` —
§6.5) → `send_audio(title=s.json["name"],
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

**TTI** (`ai/tti.py`) — **DISABLED 2026-10-26** (button removed from the AI menu,
state removed from the conversation; `import together` + `together.api_key` moved
inside the handler so the module imports without the SDK, which is no longer
installed). If re-enabled: `together.Image.create(prompt, model="stabilityai/
stable-diffusion-xl-base-1.0", 512×512, steps=40, seed=6616)` → b64decode →
`send_image`; blocking SDK call (not in executor). Dead imports: `LLMChain`,
`PromptTemplate`, `ChatOpenAI` (unused). `ai/tti_replicate.py` = unused alternative
(replicate SDK, lazy import, SDK not installed).

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
- Current DB (since 2026-10-27): `baaham-postgres` container,
  `127.0.0.1:5432/harzad_db` (user `baaham`, pgAdmin `baaham-pgadmin` :5050).
  Supabase stack unreachable from this box — user commented out its `.env`
  `DB_URI` and pointed at this DB instead. The old `harzad_test_pg` (5437)
  container is Exited and retired. Tests (`tests/conftest.py`) pin the same
  URI before import.

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
| spotdl | `>=4.2,<5` | 4.5.2 → **4.6.0** ✅ | 4.6.0 | minor; applied 2026-10-26; `utils/Spotidl.py` still imports cleanly on 4.6 internals |
| validators | `>=0.34,<0.37` | 0.34.0 → **0.36.0** ✅ | 0.36.0 | un-pinned + bumped 2026-10-26; `validators.url` verified |
| pillow | `>=10.4` | 10.4.0 → **13.0.0** ✅ | 13.0.0 | `<11` pin removed (round 1 → 11.3, moviepy-capped); moviepy dropped in round 2 → 13.0.0; prepare_cover + avatar path verified |
| yt-dlp | `>=2024.12.23` | 2026.10.21 | **2026.10.21** | current |
| moviepy | `>=2.1.1,<3` | 2.2.1 → **dropped** ✅ | 2.2.1 | **hard conflict**: moviepy ≤2.2.1 needs pillow <12, instagrapi 3.x needs pillow ≥12.2. Only used to read video duration → replaced by `mutagen` (was already a transitive dep of spotdl) |
| pydub | `==0.25.1` | 0.25.1 | 0.25.1 | frozen forever (stdlib `audioop` gone in 3.13) → **Python must stay 3.12** |
| langchain | `>=1,<2` | 0.3.30 → **1.4.6** ✅ | 1.4.7 | applied 2026-10-26 (round 2); dead `LLMChain`/`PromptTemplate` imports deleted first |
| langchain-community | — (dropped ✅) | 0.3.31 → removed | 0.4.2 | imported by nothing in the repo |
| langchain-core | `>=1,<2` | 0.3.86 → **1.6.10** ✅ | 1.7.0 | applied with the stack; pulls the langgraph 1.x chain |
| langchain-openai | `>=1,<2` | 0.2.14 → **1.7.2** ✅ | 1.7.2 | legacy `ChatOpenAI(openai_api_base=, openai_api_key=)` kwargs **still accepted** without deprecation — `chat.py` unchanged; pulls openai SDK **3.37.0** |
| psycopg2-binary | `>=2.9.9,<3` | 2.9.13 | 2.9.13 | current |
| replicate | `>=0.23.1,<1` | 0.34.2 → **removed** ✅ | 1.0.7 | TTI disabled 2026-10-26; only the dead `tti_replicate.py` used it (now lazy-imports) |
| together | `>=1.3,<2` | 1.3.14 → **removed** ✅ | 3.0.0 | TTI disabled 2026-10-26; `tti.py` now lazy-imports the SDK |
| instagrapi | `>=3,<4` | 2.1.5 → **3.2.1** ✅ | 3.2.1 | applied 2026-10-26 (round 2); import surface used by this repo (Client, delay_range, set_proxy, full exceptions list, `.model_dump()`/`.dict()`) intact on 3.2.1 + pydantic 2.12. Live login untestable: `INSTAGRAM_USER`/`INSTAGRAM_PASSWORD` empty in `.env`. 3.x login is the "Android CAA flow" with legacy fallback |
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
- **langchain 1.x stack**: langsmith, tiktoken, openai 3.37, langgraph 1.2 +
  checkpoint/prebuilt/sdk, anyio 4.x (was 3.7), pydantic 2.12.5.
- **instagrapi 3.x**: httpx2 2.16 (+ httpcore2, httpx2-jsfetch), truststore —
  the 3.x client is a new "Android CAA flow" implementation.
- **moviepy chain gone** (round 2): imageio, imageio-ffmpeg, numpy, proglog,
  decorator all left the lock with it.
- **ptb 22.8** → httpx, httpcore, certifi, typing-extensions.

### 4.3 Python

`.python-version` = 3.12; `requires-python = ">=3.12,<3.13"` — the cap exists because
`pydub 0.25.1` imports stdlib `audioop` (removed in 3.13). .venv python = 3.12.8,
host pyenv also has 3.13.7 (used by the tunnel helper only). uv 0.5.9.

### 4.4 Dev group

pylint, black, isort, bandit, autopep8, plus **pytest 9.1.1 + pytest-asyncio 1.4.1
(added 2026-10-26)** for the test phase. `tests/` exists and is green (183 tests,
`addopts = -m 'not live'`; live set runs with `pytest -m live`) — see §11 status.

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
- **/tmp is an 8 GB tmpfs — keep it lean** (2026-10-27 incident): a stuck
  `pw-play -r` (local pipewire 1.6.9 build from last night's audio debugging)
  held `/tmp/opencode/tone1k.wav` open for *writing* and grew it to 7.9 GB
  over ~12 h, filling the tmpfs around 10:30 — ENOSPC broke every /tmp write
  (shell heredocs, yt-dlp/ffmpeg temp files). Killed the process, deleted
  the file. Check `df -h /tmp` before long work; find offenders with
  `du -sh /tmp/opencode/* | sort -rh`. Root fs is also 95% full (9.6 G free
  as of 2026-10-27).
- **pkill footgun** (hit twice): always bracket the pattern
  (`pkill -f "python main[.]py"`); never combine pkill + relaunch in one command line
  — the relaunch line's own cmdline matches and kills the shell.
- **Docker**: `baaham-postgres` = **the bot's Postgres**
  `127.0.0.1:5432/harzad_db` (baaham/baaham_password; pgAdmin `baaham-pgadmin` :5050)
  — user-migrated 2026-10-27 after Supabase went unreachable. Do not stop it.
  `harzad_test_pg` (5437) retired (Exited 2026-10-27; died unguarded — same
  failure mode as before). Local Supabase stack (`supabase_*_1819`), adminer,
  db/db_backup (mysql) = unrelated.
- Supabase stack (`db.mwzxysevdedjuhkidouv.supabase.co`) unreachable from this
  box — that is why the bot moved to `baaham-postgres`.
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
5. **Spotify**: search in executor (pure sync HTTP) → per-track
   `await spotdl.downloader.pool_download(song)` on the running bot loop →
   send_audio. **Never** the sync spotdl download API: `Downloader.__init__`
   does `set_event_loop(its loop)` in the main thread and PTB's `__run`
   picks that loop up via `get_event_loop()` and runs the bot on it, so the
   sync API's `run_until_complete` raises "This event loop is already
   running" — and `asyncio.gather()` in `download_multiple_songs` schedules
   the download task on the running loop *before* the check, so the song
   downloads as an orphaned task (mp3 on disk, no sendAudio, user sees
   "Something went wrong"). First crash 2026-10-27 09:48 (worker thread had
   no current loop; the "fix" — binding spotdl's loop in the worker — only
   moved the crash, because that loop *is* the bot's loop); second incident
   10:03 (orphaned download + double instance). Root cause fixed 2026-10-27:
   async API on the bot loop + main-thread loop reset in `main.py` +
   single-instance lock. Regression tests in `tests/test_12_handlers.py`
   (sync API raises, pool_download awaited on the running loop) and
   re-verified live in `tests/test_15_live_features.py`.
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
| `ai/tti.py`, `ai/tti_replicate.py` | **feature disabled 2026-10-26** (lazy SDK imports, UI wiring removed); dead langchain imports removed in the majors round |
| `ai/tti_replicate.py` | entirely unused handler (replicate path, SDK no longer installed) |
| `utils/custom_context.py` MyContext | wired nowhere (`.context_types()` commented in main) |
| `utils/decorators.py:restricted` | unused; **int/str ADMINS bug fixed 2026-10-26** (would have denied everyone, incl. admins) |
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

1. **DB availability**: `harzad_test_pg` died unguarded twice (2026-10-26,
   2026-10-27) while the bot pointed at it — every handler update would have
   failed at `sync_user` → "Something went wrong". Bot + tests now use
   `baaham-postgres` (`127.0.0.1:5432/harzad_db`) — same unguarded-container
   risk remains; no liveness guard exists for any DB.
2. `>50 MB` fallback link dead (`HOSTNAME=http://localhost/`) — Copy button only.
3. Tunnel URL churn (~20 min) + client-side menu-button staleness → 502 window on
   phone tap; workaround = fresh URL message in chat. Stable domain = backlog #1.
4. Watchdog + ncx live in `/tmp` (wiped on server restart) → rebuild by hand =
   backlog #1.
5. ~~TTI uses together.ai via a possibly-dead SDK path.~~ Resolved by disabling TTI
   (2026-10-26, commit `c398813`): feature off, deps dropped, re-enable later if wanted.
6. Instagram **credentials are empty in `.env`** (`INSTAGRAM_USER`/
   `INSTAGRAM_PASSWORD`) — discovered 2026-10-26 while verifying the instagrapi
   3.2 bump (login raises `BadCredentials` before any network call; identical
   behavior on 2.1.5). No `login/` session exists either. The IG feature is
   code-ready but untestable until the admin fills those in.
7. ~~Spotify download unverified~~ **Resolved 2026-10-26**: live spotdl 4.6.0
   search + download verified (`tests/test_15_live_features.py`, ~4 min run).
   Re-verified 2026-10-27 after the loop-hijack fix (item 14, same live
   test, ~3 min).
8. ~~No tests anywhere; no CI.~~ **Resolved 2026-10-26**: `tests/` — 183
   unit/integration tests + 4 live tests; CI still none.
9. **Pinterest live test needs a real pin URL** — Pinterest serves no
   server-rendered pin ids to plain scrapers (ideas/profile pages are JS
   shells; search engines + Reddit blocked via the box proxy). Live test
   skips unless `PINTEREST_TEST_PIN` is set. Mechanism is covered by the
   shared yt-dlp engine (verified live on YouTube) + handler-branch unit tests.
10. **`next_proxy` is broken**: `.env` `PROXY_LIST=None` (the literal
   string), so `random.choices()` returns a single character. Only the IG
   rate-limit recovery path uses it — harmless until IG creds exist.
11. **`get_client` double login**: the no-session path calls `client.login()`
   twice (dump, then validate). Pinned in `tests/test_14` on purpose; review
   whether the second call is needed once IG is live.
12. **`prepare_link` double slash**: `HOSTNAME` carries a trailing slash →
   `http://localhost//videos/...`. Only matters if the >50 MB fallback link
   is ever fixed (issue #2).
13. **YDL_OPTS outtmpl concatenation**: `DOWNLOAD_VIDEO_PATH` has no trailing
   slash, so files land as `downloads/videos<Title>.mp4` (flat in
   `downloads/`, not in a `videos/` subdirectory). Both flows (chat + mini
   app) rely on it being consistent — it is (shared `YDL_OPTS`).
14. ~~Spotify "not working again" + double listener (2026-10-27 10:01–10:03)~~
    **Resolved 2026-10-27**: two problems compounded. (a) A second instance
    (user's own `uv run python main.py`, 10:01:30) caused a 409 Conflict
    storm and duplicate handling — update 743386062 was processed by BOTH
    processes. (b) Independently, the Spotify handler crashed with
    `RuntimeError: This event loop is already running`: spotdl's
    `Downloader.__init__` does `set_event_loop(its fresh loop)` in the main
    thread, and PTB's `Application.__run` runs the bot on that same loop via
    `get_event_loop()`; the sync download API's `run_until_complete` hit the
    running loop, and `asyncio.gather()` had already scheduled the download
    task on it — the mp3 landed on disk as an orphan (no sendAudio; user saw
    "Something went wrong" + 👎 while the download finished 20 s later).
    Fix: handler awaits `downloader.pool_download` (spotdl's async per-song
    unit, slow sync steps offloaded to the default executor) on the bot
    loop; `main.py` resets the main thread's event loop right after Spotdl
    init; single-instance `flock` on `run/harzad_bot.lock` — a second start
    now exits with the holder's pid (also makes the watchdog's
    pkill+relaunch fail-safe: surviving old instance ⇒ new one refuses
    instead of double-listening). Side find same day: /tmp tmpfs 100% full
    from a stuck `pw-play` tone file (killed + deleted; §5).

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
| 12 | chat handlers × 6 | handler-level with mocked bot (PTB `TestBot`/httpx mock) | live per-feature: YT video, YT mp3, Spotify, Pinterest, AI chat (TTI disabled); **IG blocked on empty `.env` creds** (open issue #6) |
| 13 | `main.py` bootstrap | post_init order; menu-button only-when-URL logic | real boot (already routine) |
| 14 | `utils.instagram`/`InstagramClient` | exception→LoginException mapping | IG login + one media (needs creds/session) |

**Status 2026-10-26 (suite built), 2026-10-27 (production-path fix) — all modules green.**

- 183 unit/integration tests: `tests/test_01_settings.py` …
  `tests/test_14_instagram.py` + `tests/conftest.py`. Env matrix runs in a
  scratch package copy (load_dotenv resolves `.env` from the importing
  module, not cwd). Mini app via `aiohttp.test_utils.TestClient` with a
  stubbed `_ytdlp_run`. DB tests hit the bot's own Postgres
  (`baaham-postgres`) with unique 32-bit tg_ids, cleaned up in teardown. Handler tests use a fake `YoutubeDL` +
  ffmpeg-generated fixtures (`tests/fixtures/small.mp4|.mp3`). `respx` was
  never needed (mocking at module boundaries).
- Live (`pytest -m live`): YT video ✅ (mp4 ≤720p + webp thumb, mutagen
  duration, `prepare_cover` → JPEG ≤190 KB), YT mp3 ✅
  (FFmpegExtractAudio via `/usr/bin/ffmpeg`), Spotify ✅ (real search +
  download, "concerto for rain" — **through the production
  executor path**, after the 2026-10-27 thread-loop crash), AI chat ✅ (production mini app against
  vLLM), Pinterest ⏸ (open issue #9), IG ⏸ (open issue #6 — left open by
  user request).
- Bugs found by the suite and **fixed**: `escape_md` incomplete MDv2
  escaping (`re.escape` special-set drifted in Python 3.7+; `>`, `=`, `!`
  and backticks went unescaped → Telegram parse errors on such text);
  `restricted` comparing int user id against string ADMINS;
  `utils/instagram.py` crashing with `FileNotFoundError` on first-ever
  login failure (`os.remove` of a session file that does not exist — now
  guarded); `BadCredentials` (what instagrapi 3.x raises for empty/invalid
  creds) added to the `LoginException` mapping in `utils/InstagramClient.py`.
  **2026-10-27, found in production (not by the suite):** spotdl 4.6's
  `download_multiple_songs` calls `asyncio.gather(*tasks)` without a loop
  argument → in the handler's worker thread Py3.12's `get_event_loop()`
  raises `RuntimeError` (the main thread silently creates a loop, which is
  why the first live test passed and the bot crashed). Fix: bind
  `spotdl.downloader.loop` in the worker thread; the live Spotify test now
  goes through `await_download_from_spotify` so it can never regress this.

## 12. Git & workflow

- `main` (pushed) = `ae7af69` (KB majors round); test phase landed 2026-10-26. Phase merges: C `162e2d1`/`98ad365`,
  A `cd2b35f`, B `f5d1ffb`. Stale local branches: `mini-app`, `inline-keyboards`,
  `ux-polish`, `uv-migration` (all merged); remote extras: `clean`, `develop`.
- Phase workflow (user-fixed): implement on branch → **live gate** → user confirms →
  commit → merge `--no-ff` to main → push. Deps + test phase follows the same
  confirm-first rule per the user's 2026-10-26 instruction.
- Commits: normal prose (caveman mode is conversation-only).
- `uv` project: `package = false` (app repo, deps only).

## 13. Dependency update plan (status as of 2026-10-26)

Ordered lowest-risk first; after each step: `uv lock` + import smoke + the matching
test from §11. **User decision 2026-10-26: disable TTI (together/replicate) instead
of upgrading it; keep chat; apply the low-risk ones.**

1. ✅ **Dev group**: added `pytest` 9.1.1, `pytest-asyncio` 1.4.1 (commit `c398813`).
2. ✅ **Already-latest no-ops**: PTB 22.8, dotenv 1.2.4, sqlalchemy 2.1.6, alembic
   1.20.1, yt-dlp 2026.10.21, moviepy 2.2.1, psycopg2 2.9.13, requests 2.34.2,
   aiohttp 3.14.5, ffmpeg 1.4, pydub 0.25.1 — lock verified.
3. ✅ **validators 0.34 → 0.36** (commit `c398813`; `validators.url` verified).
4. ✅ **spotdl 4.5.2 → 4.6.0** (commit `c398813`; `utils/Spotidl.py` internals +
   `Spotdl` construction with Spotify auth verified).
5. ✅ **pillow 10.4 → 11.3** (resolver cap, not 13 — moviepy/imageio chain;
   `prepare_cover` + avatar resize verified).
6. ✅ **langchain stack 0.3 → 1.x** (commit `f4d4586`): dead
   `LLMChain`/`PromptTemplate` imports deleted; legacy `ChatOpenAI` kwargs
   verified still accepted (no deprecation) so `chat.py` unchanged;
   `langchain-community` dropped; live vLLM round-trip green (direct +
   production Mini App).
7. ~~together 1.3 → 3.0~~ **superseded**: feature disabled, dep removed.
8. ✅ **instagrapi 2.1 → 3.2.1** (commit `f4d4586`): import surface + client
   construction verified; `.dict()`/`.model_dump()` intact on pydantic 2.12.
   **Live login untested — `.env` creds empty** (open issue #6; not a regression).
9. ~~replicate 0.34 → 1.0.7~~ **superseded**: dep removed (only the dead path used it).
10. ✅ Final smoke for this batch: full boot + mini-app health + live anonymous
    AI chat round-trip over the public tunnel (all green 2026-10-26 10:33).

Standing constraints: Python stays 3.12 (pydub/audioop); `ffmpeg==1.4` stays pinned;
vLLM is loopback-only and never behind the proxy; `YT_PROXY` stays
`http://127.0.0.1:10808`. Practical note: `uv sync` wheel downloads time out on the
box's direct egress — use `HTTPS_PROXY/HTTP_PROXY=http://127.0.0.1:10808
UV_HTTP_TIMEOUT=180`; and a widened pin does not move a locked version on its own —
force with `uv lock --upgrade-package <name>`.
