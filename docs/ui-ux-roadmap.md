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

Gate: full click-through of every section (no typing at all) + one real download.

### Phase B — Telegram Mini App (Web App) (last, separate project)

Target: the app-like feel of major 2025+ bots.

- Small HTML/JS frontend hosted on HTTPS (Vercel / GitHub Pages).
- Opened from a persistent **chat menu button** (`set_chat_menu_button`,
  `MenuButtonWebApp`) and/or an "Open App" inline button.
- Telegram theme variables (auto dark/light), `initData` user auth,
  media sent via the Telegram Web Apps JS SDK.
- Best fit for heavy flows: AI chat input, download options with forms/toggles.

Gate: auth round-trip + one download completed from inside the Web App.

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
