# Bot API feature opportunities — harzad_bot

Audit date: 2026-10-24
Source: official changelog, `core.telegram.org/bots/api-changelog` — Bot API 7.0 (Dec 2023) → 10.3 (Aug 2026, current)
Stack: `python-telegram-bot 22.8` (newest PyPI release at audit time) = full **Bot API 10.0** support.
Bot API 10.1–10.3 (Jun–Aug 2026) have **no PTB support yet** (checked against the installed library).

## Already in use

| Feature | API | Where |
|---|---|---|
| Message reactions (👍 success / 👎 failure) | 6.0 / 7.0 | Phase C — `utils/helpers.py:react_message`, global error handler |
| Content protection (`protect_content` on all media) | 6.2 | Phase C — `send_image`, all `send_*` calls |
| Bot profile (commands, description, short description) | 6.0–7.0 | `main.py:post_init` |
| HTML parse mode + captions on media | — | Phase C |
| Button color style (`style: primary/success/danger`) | 9.4 | Phase A — `utils/keyboards.py:_action()` |
| Copy-to-clipboard button (`CopyTextButton`) | 7.11 | YouTube "file too big → link" message: **Copy link** button (`bot/handlers/youtube/download.py`) |
| Video cover (`send_video(cover=…)`) | 8.3 | yt-dlp thumbnail (`writethumbnail`) downscaled to 480px/<200 KB via `utils/helpers.py:prepare_cover` |
| Bot profile photo via API (`set_my_profile_photo`) | 9.4 | `assets/logo.png` set from `main.py:post_init` (downscaled to 512×512 JPEG in memory) |

## Usable right now (PTB 22.8, import verified)

| Feature | API | Use in harzad |
|---|---|---|
| Custom emoji button icons (`icon_custom_emoji_id`) | 9.4 | Animated premium-emoji icons on menu buttons. **Requires the bot owner's account to have Telegram Premium** — waiting on an emoji id |
| Caption above media (`show_caption_above_media`) | 7.4 | Cosmetic: title/caption above the video instead of below |
| Streaming message drafts (`send_message_draft`) | 9.3 / 9.5 | Stream AI-chat replies token-by-token in one morphing message — do after chat is stable |
| Message effects (`message_effect_id`) | 7.4 | Optional celebration effect on successful download |
| Reaction removal (`delete_message_reaction`) | 10.0 | Housekeeping only |
| Polls with media, 1 option allowed | 10.0 | Future idea: playlist picker (entries as media poll options) |

Not exposed in PTB 22.8: `has_media_spoiler` (7.3) — not useful for a downloader anyway.

## Waiting on a future PTB release (Bot API 10.1–10.3)

| Feature | API | Note |
|---|---|---|
| Rich Messages (`sendRichMessage`; blocks: tables, lists, collapsible quotes, collages, slideshows) + `sendRichMessageDraft` streaming | 10.1+ | Biggest future UX upgrade, especially for AI chat |
| Disabled buttons (`disabled` on `InlineKeyboardButton`) | 10.3 | Gray out "Download" while a job runs / after delivery; kills double-tap re-downloads |
| `force_reply` on `InlineKeyboardMarkup` | 10.3 | Combine input prompt + Back/Home buttons on one message |
| Ephemeral messages | 10.2 | Group-chat feature; not our use case |
| Guest mode | 10.0 | In the library, unused — not our use case |

## Phase B (Mini App) features

- `WebApp.downloadFile` (8.0) — **native download popup from the Mini App**; killer feature for a downloader app.
- Home-screen shortcut: `addToHomeScreen` / `checkHomeScreenStatus` (8.0) — "Harzad" icon on the user's phone.
- Fullscreen mode + safe-area insets (8.0); loading-screen customization via @BotFather (8.0).
- `WebAppUser.photo_url` (8.0), `WebApp.hideKeyboard` (9.1), bottom-bar color (7.10), Device/SecureStorage (9.0).
- ⚠️ **Origin lock (10.2): enforced on ALL Mini Apps since 2026-07-20** — the app must be single-origin, no cross-origin API calls from the app. Architect from day one.

## Not relevant (skipped)

Star subscriptions / payments (7.4, 8.0), paid media, gifts, business accounts (7.2+), checklists (9.1), communities (10.2), live photos (10.0), bot-to-bot communication (10.0), stories (business only).
