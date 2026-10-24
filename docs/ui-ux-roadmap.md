# Harzad Bot — UI/UX Upgrade Roadmap

Date: 2026-10-24
Stack: Python 3.12 · python-telegram-bot 22.8 · Bot API 10.0 (full support)

## Current state

The bot navigates entirely through **reply keyboards** (on-screen text buttons) matched
against typed text with regex, across 12 conversation states.

Known weaknesses:

1. **Double-escaped text**: every bot message goes through `send_md_msg` → `escape_md()`,
   so no Markdown formatting can ever render — instructions show literal backticks
   (`\`@username\``), and user names get ugly escapes (a name like `:{` renders `:\{`).
2. **Message spam**: downloads send media and caption as two separate messages;
   progress messages are deleted and re-sent instead of edited to a final state.
3. **No error UX**: no PTB error handler is registered. Any unhandled exception
   (e.g. DB outage) silently drops the update; the user gets nothing.
4. **Fragile input**: button text is free-typed; a typo or extra space breaks navigation.
5. **Fallthrough bug**: `instagram/download.py` falls into a stray
   "This is the image" message after profile-picture downloads and invalid links.
6. **Wrong keyboard** in `youtube/download.py` / `youtube/mp3.py`
   (sends the Pinterest-state keyboard — same buttons today, wrong by construction).

## Phases

### Phase C — UX polish (first, small effort)

Target: the bot *feels* modern without changing its architecture.

- [x] **HTML parse mode for bot messages** — new `send_html_msg` helper; user-provided
      data escaped with `escape_html()`; live templates converted from MarkdownV2 to HTML
      (`<b>`, `<code>`). Fixes the double-escape mess (#1).
- [x] **Cleaner `/start` onboarding** — reworded greeting (no raw command echo),
      HTML formatting, menu below.
- [x] **Real `/help` text** — replaces the placeholder "This is a help message".
- [x] **Caption on media** — photo/video/audio carry their caption directly
      (one message instead of two), keyboard attached to the media message (#2).
- [x] **`protect_content` on all sent media** — downloaded content can't be forwarded
      or saved-as from the chat.
- [x] **Reactions** — 👍 when a task succeeds, 👎 on failures. (✅/❌ turned out to be
      rejected by Telegram — `Reaction_invalid`; only reaction-picker emojis are allowed.
      Reactions go through a defensive `react_message()` helper that never raises.)
- [x] **Global error handler** — logs the full traceback and replies
      "Something went wrong 🤯" + 👎 reaction instead of silently dropping (#3).
- [x] **Bug fixes** — instagram fallthrough (#5), wrong keyboard in youtube handlers (#6),
      `/start` shadowed by `filters.TEXT` in substates, `/help` stranded the state,
      `ydl.extract_info` unguarded, empty-playlist hang (bare `https://youtube.com`),
      reply keyboards attached to `editMessageText` (400 `Inline keyboard expected`).

Gate: **PASSED 2026-10-24.** `/start`, `/help`, navigation, bad-link handling and real
downloads verified live in Telegram (including the stuck-menu and duplicate-message bugs
from the first test round, fixed and re-verified).

### Phase A — Inline keyboards + callbacks (after C is confirmed)

Target: the standard modern-bot navigation pattern.

- Replace all reply keyboards with `InlineKeyboardMarkup` + `CallbackQueryHandler`.
- Callback data = machine IDs (e.g. `nav:ig:download`), no regex on typed text (#4).
- Menus edit in place (`edit_message_reply_markup`) instead of new messages.
- `answer_callback_query` for click feedback.
- Keep `ConversationHandler` (it works with callback queries unchanged).
- Scope: `utils/keyboards.py`, `bot/conversations/main.py`, all handler entry points.

Implemented 2026-10-24 on `inline-keyboards` branch:

- `CB_*` callback-data constants in `utils/constants/keyboards.py`; callback data
  names the destination (`nav:yt`, `nav:ig`, …).
- All keyboards rebuilt as `InlineKeyboardMarkup` in `utils/keyboards.py`;
  action/section buttons use `style="primary"`, Back/Home neutral.
- Single dispatcher `navigate()` in `bot/navigation.py` (NAVIGATOR table):
  in-place `editMessageText` when the current message is text, fresh menu on
  media messages, `answer_callback_query` always, "not modified" swallowed.
  Registered as `CallbackQueryHandler` first in all 12 states; stray callbacks
  answered by a group-1 handler in `main.py`.
- Old reply-keyboard menu/`before_*` modules left in place as dead code
  (deletion needs separate approval).

Gate: **PASSED 2026-10-24.** Full click-through of every section plus real
YouTube download verified live in Telegram — in-place menu edits, video arrives
as a true `video` message (player + streaming + cover), caption carries the
video title, `>50MB` fallback offers a one-tap copy-link button.

### Phase B — Telegram Mini App (Web App) (last, separate project)

Target: the app-like feel of major 2025+ bots.

- Small HTML/JS frontend (SPA) + JSON API served by an aiohttp server
  **embedded in the bot process** — single origin, required by Bot API 10.2's
  menu-button origin lock. Public exposure via a free `localhost.run` SSH
  reverse tunnel (no VPS/Vercel: they can't reach the loopback vLLM / yt-dlp
  proxy path). A tunnel watchdog auto-reconnects and re-syncs the URL.
- Opened from a persistent **chat menu button** (`set_chat_menu_button`,
  `MenuButtonWebApp`), re-registered from `.env` at every boot.
- Telegram theme variables (auto dark/light), `initData` HMAC auth
  (WebAppData scheme), browser/anonymous fallback mode outside Telegram.
- v1 scope: **Download tab** (YouTube link → job → video delivered to the
  chat + `tg.downloadFile` "Save to device") and **AI chat tab** (local vLLM).

Gate: auth round-trip + one download completed from inside the Web App.
Gate: **PASSED 2026-10-24.** Real phone (Telegram-Android 13.1.0): initData
verified (`user_id=575570675`, name displayed), AI chat round-trip OK, and a
full download from the Download tab ("Deadlock Tips For Noobs #11", 8.3 MB
mp4) delivered to the chat as a playable video (`sendVideo` with cover,
message 9996). "Save to device" streams via `Content-Disposition:
attachment` + CORS for `https://web.telegram.org` (Range-capable).

## Decision log

- 2026-10-24: Order fixed as C → (confirm) → A → (confirm) → B.
- 2026-10-24: PTB 21.11.1 → 22.8 (Bot API 10.0); code audit found zero
  breaking-change hits, so the upgrade was a pure dependency bump.
- 2026-10-24: Phase C gate passed. Live testing caught three PTB 22 / Bot API
  landmines the code audit couldn't: `Message.react()` doesn't exist (use
  `Bot.set_message_reaction`), ✅/❌ aren't valid reaction emojis, and
  `editMessageText` refuses reply keyboards (only inline ones are editable).
  Also pre-existing latent bugs: `filters.TEXT` shadowed `/start` in every
  substate, and yt-dlp resolves a bare domain URL to an empty "recommended"
  playlist, which silently hung the progress message.
- 2026-10-24: Audited Bot API 7.0 → 10.3 against PTB 22.8 (newest release);
  results in `docs/bot-api-feature-opportunities.md`. Button color styles
  (9.4) adopted in Phase A. AI chat backend switched from dead Ollama to the
  local vLLM server (`LLM_BASE_URL=http://127.0.0.1:40002/v1`, model
  `gemma4`), deprecated `LLMChain` removed, call now async (`ainvoke`).
- 2026-10-24: Phase A gate passed and merged to `main`. Live testing found more
  Bot API landmines: `sendVideo` demotes webm uploads to plain `document`
  (no player/cover) → YouTube format is now mp4-only
  (`bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]`), no ffmpeg transcode;
  yt-dlp writes YouTube thumbnails as `.webp`, which both `thumbnail` and
  `cover` reject → local re-encode to a small JPEG (`prepare_cover`);
  `setMyProfilePhoto` needs `InputProfilePhotoStatic` wrapping an
  `InputFile(..., attach=True)`; a flaky proxy killed first-boot
  `getMe` (builder default `bootstrap_retries=0`) → `run_polling(bootstrap_retries=-1)`.
  Also fixed: media captions were bot-id-only → now carry the video title
  (playlist loop previously used the playlist title for every item).
  Known open item: the `>50MB` link fallback points at `HOSTNAME` which is
  `http://localhost/` in `.env` — dead from outside; copy button makes it
  one-tap until a real host is configured.
- 2026-10-24: Phase B hosting settled on the free `localhost.run` SSH
  reverse tunnel (user rejected VPS/Vercel). A watchdog script supervises
  the tunnel: auto-reconnect, URL extraction, `.env` sync, bot restart,
  heartbeat. Live operation showed localhost.run **re-issues a new URL
  mid-session (~every 20 min)** — the old one dies instantly with
  "no tunnel"/502 — so the watchdog polls for fresh URL lines and
  re-syncs the bot on every change, not just on process death. The bot and
  watchdog must run detached (`setsid`): the harness reaped a background
  shell's whole process group once, killing bot + ssh + watchdog together.
  Known open item: the URL still changes on every real drop; a stable
  free/paid domain is the post-gate hardening.
- 2026-10-24: Phase B gate passed (phone: auth + AI chat + real download,
  see gate above). Live phone testing found: (1) current Telegram clients
  no longer embed `auth_date` in the `user` object — the freshness check
  must read the **top-level `auth_date` param** (the hash had been passing
  all along; only the TTL read was stale-shaped); (2) the client blob now
  carries an undocumented `signature` param — the verifier hashes all
  params except `hash` generically, so it matches without special-casing;
  (3) the SPA labelled assistant turns `role: "bot"`, which LangChain
  message tuples reject → server normalizes roles (`bot`→`assistant`,
  `user`→`human`).
