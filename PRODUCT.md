# PRODUCT.md — Harzad Bot · Complete UX Handoff

**For:** UX designer
**From:** Engineering (implementer)
**Date:** 2026-10-27
**Status:** AS-IS specification + design brief. The current UX is under-performing; users bounce. Design the whole product UX. Engineering will implement your spec verbatim.

**How to use this doc:**
- Sections 1–3: what the product is and who uses it.
- Section 4: **the complete AS-IS walkthrough** — every flow, every message, every button, every latency. Design against this, not from memory.
- Section 5: real latency numbers (measured, not theoretical).
- Section 6: known flaws, ranked. You may fix, keep, or redesign any of them.
- Section 7: **hard platform constraints** — the Telegram Bot API is not a webview. Design inside these walls; they cannot be moved without a Mini App.
- Section 8: what is cheap vs expensive to build. Use it to prioritize.
- Section 9: **the design brief — exactly what you must deliver back.**
- Appendix A: full inventory of current messages, keyboards, callback IDs, states.

---

## 1. Product Overview

**Harzad** (`@harzad_bot`) is an all-in-one Telegram media assistant:

| Capability | What it does |
|---|---|
| Media download | YouTube (video 720p/360p/mp3), Instagram (post/reel/story/profile pic), Pinterest, TikTok, Spotify (track/album/playlist → MP3) |
| Music discovery | Text search ("starboy" → 3 candidate cards with cover art), 30s preview playback, inline search (`@harzad_bot starboy` in any chat/group) |
| AI | Chat with a local LLM (gemma4, ~5–10 s per answer, no memory); text-to-image (currently **dead** — see §6.3) |
| Mini App | A small web app ("Open Harzad" chat-menu button): Download tab (job-based downloads + file links) and Chat tab (AI) |

**Product goals (owner's intent):**
1. **Zero friction** — a pasted link should "just work" from anywhere, even before the user ever pressed /start. (This is implemented; the rest of the UX has not caught up.)
2. **Discovery, not just download** — users without URLs should be able to find and get music.
3. **Feel fast and alive** — no silent waits, no dead buttons, no confusion about what the bot is doing.

**Positioning:** a utility bot people keep in a chat, use 2–3 times a week, and occasionally @-mention in groups. It is not a power tool. Every interaction should complete in as few taps as possible and never require the user to understand the bot's internal menu structure.

## 2. Target Users & Jobs to Be Done

**Primary users**
- **The Sharer** — has a link (forwarded from a friend, copied from an app) and wants the file in Telegram. Doesn't care what platform or format. Taps once, gets file.
- **The Music Fan** — knows a song but not the link. Wants to find it, check it (30s preview), get the MP3. Sometimes shares a track with friends via inline mode.
- **The Saver** — collects reels/IG posts/pins for later. Wants clean media without watermark.

**Jobs to be done (priority order)**
1. "Get me this media I'm pointing at" (link in) → (file out)
2. "Get me this song I can name" (name in) → (MP3 out)
3. "Check what this is before I commit" (preview/cover)
4. "Ask something / make an image" (secondary, low frequency)
5. "Do any of the above in a group without cluttering it"

**Key insight:** the bot is judged on *job 1 and 2 speed*. Everything else is background. If a job takes >5 s, the user must be continuously reassured; if >30 s, they must be able to intervene (cancel, switch task).

## 3. Platform & Deployment Context

- Runs as a **single process** on one server; all bot logic, one Postgres DB (user/message logging), local vLLM for AI, egress through a **flaky HTTP proxy** (this is why some operations are slow — see §5).
- **No conversation persistence**: all chat state (which menu screen the user is on, search results, pending downloads) lives in memory. A process restart (deploys happen) silently resets it. Any UX that depends on "remembered" state must degrade gracefully (see §6.11).
- Bot is **English-only** today. User base skews Persian (Iran). Decide: keep English, design bilingual copy, or flag localization as P2. (Copy must be designed language-agnostically: short strings, emoji as universal affordances.)
- **Group chats** matter: the bot is @-mentioned with links and used inline in groups. It must be safe to use in a group: buttons on a card resolve for the user who created them (a random group member tapping "Download" currently gets a "Results expired" alert — §6.9).

---

## 4. Current UX — Complete AS-IS Walkthrough

### 4.1 Entry points (all of them)

| # | Entry | What happens today |
|---|---|---|
| E1 | `/start` (any time, any state) | Menu message: greeting + user ID + 5 primary buttons (Instagram / Music 🎵 / Pinterest / Youtube 🎞 / AI 🤖). Re-sends menu (new message, not edit). |
| E2 | `/help` | How-to text + menu buttons. |
| E3 | **Paste any media link** (any state, or *before ever sending /start*) | Universal dispatcher: detects platform, routes. YouTube → format-picker buttons; others → download starts immediately. Non-media link or plain text → falls through to that state's input handler. |
| E4 | **Type a song name** in the Music states (MUSIC or MUSIC_SPOTIFY) | Text search → live status message with Cancel button → up to 3 track cards. |
| E5 | **Inline query**: `@harzad_bot <song>` in any chat/group | Native Telegram inline results (3 track articles with cover thumbnails). Tapping one delivers the track card into *that* chat. |
| E6 | Menu button **"Open Harzad"** (chat menu bar) | Opens the Mini App (web app, Download + Chat tabs). |
| E7 | **Any menu button** (inline keyboard) | Navigates in place (edits the clicked message) — no new messages per menu hop. |
| E8 | Tapping buttons on a **track card** (download/preview/lyrics/open-on-spotify) | Works in any state, even after leaving the menu. Lyrics = "coming soon" toast. |

**There is no `/cancel`, no `/end`, no way to exit a state except re-navigating via buttons or `/start`.**

### 4.2 Conversation state machine

One conversation per (chat, user). 12 states; `state` = where the user "is":

```
                        ┌──────────────────────────────┐
        /start (entry)  │  START (0) — main menu       │
        /start (in any  │  [IG] [Music] [Pin] [YT] [AI]│
        state) ────────►└──────────────────────────────┘
   every state: [yt-format cb] [song-card cb] [search-cancel cb]
                [nav cb] [link-dispatcher] [/start /help] [state TEXT handler]
```

| State | Reached by | Prompt message | Waiting for |
|---|---|---|---|
| START (0) | /start, Home | Menu (5 buttons) | button tap |
| INSTAGRAM (1) | IG button | "Choose an option 👇" + [Download] [Back] [Home] | button tap |
| INSTAGRAM_DOWNLOAD (2) | Download button | "Send me the url of the post, story or reel… or @username for profile picture" | **text** |
| MUSIC (3) | Music button | "Choose an option 👇" + [Spotify] [Back] [Home] | button tap **or song name (search)** |
| MUSIC_SPOTIFY (4) | Spotify button | "Send me the url of the song, album or playlist…" | **text** (URL → download; song name → search) |
| PINTEREST (5) | Pin button | "Send me the url of the Pinterest video…" + [Back] [Home] | **text** (directly — note: PINTEREST is both a menu *and* input state) |
| YOUTUBE (6) | YT button | "Choose an option 👇" + [Video] [MP3] [Back] [Home] | button tap |
| YOUTUBE_DOWNLOAD (7) | Video button | "Send me the url of the video or playlist…" | **text** |
| YOUTUBE_MP3 (8) | MP3 button | same prompt | **text** |
| AI (9) | AI button | "Choose an option 👇" + [Chat 💬] [Text to image 🖼] [Back] [Home] | button tap (TTI button is **dead**, §6.3) |
| AI_CHAT (10) | Chat button | "Start a conversation with me 🙂" | **text** (any text → LLM) |
| AI_TTI (11) | *unreachable* (button dead) | (instruction text exists) | text → prompt (engine missing) |

**Behavior rules the designer must know:**
- **Every state runs the universal link dispatcher before its own text handler.** A pasted link in the AI chat goes to the downloader, not the LLM.
- **Every state can resolve song-card buttons and the search Cancel button** (they carry their own data).
- `/start` and `/help` work in every state (reset to menu).
- A user who never sent /start has **no conversation**: link paste, nav buttons, card buttons still work (separate fallback layer); plain text is ignored.
- **No state indicator**: the user has no way to see which mode they're in. (The one "You are at the state X" message in the codebase is a debug artifact never shown to users.)
- Navigation **edits the clicked message in place** — the chat stays clean, one menu message is re-themed per screen.

### 4.3 Flow-by-flow walkthroughs

**Format:** each flow lists every screen in order with the exact current message, buttons, what the user can do, and measured/estimated latency. "(edit)" = the bot edits its own status message in place; "(new)" = new message; "(del)" = status message deleted.

#### F1 — Menu & navigation
1. `/start` → (new) HELLO + menu. Latency: 1–4 s (includes 2× DB writes before the first reply — see §5).
2. Tap any button → (edit) next screen. Latency: instant (local) + 0.5–2 s (proxy RTT).
3. Back/Home → (edit) to that screen.
- **Flaws:** 3 taps to reach "type a Spotify URL" (start → Music → Spotify); menu says "Choose an option 👇" on screens that have no obvious choice; no search box anywhere in the menu.

#### F2 — Instagram (menu path: IG → Download → paste URL)
1. Prompt: send post/reel/story URL or `@username`.
2. (new) "Processing ⏳" → (edit) "Fetching media info 🔎" → fetch (3–15 s, via instagrapi + proxy).
3a. Photo → (del) status, (new) photo with caption (post text + "🆔 @harzad_bot"), [Back] [Home]. 👍 reaction on user's message.
3b. Video/reel/IGTV → (del), (new) video (streaming, with cover). 👍
3c. Album/carousel → (del), then **one media item per message** (photo/video each with its own upload indicator), then one caption message. 👍
3d. Story → (edit) "Getting Story information…" → photo or video. 👍
3e. `@username` → (del), profile picture (HD). 👍
4. Errors: media not found / user not found → (del) status, 👎 reaction, "Media not found or unavailable…" + [Back] [Home]. Instagram session missing (server-side) → 👎 + "Something went wrong 🤯".
- **Flaws:** no progress indication for the 3–15 s fetch beyond one text edit; album dumps N messages (no "1 of 5" context); the bot's own ID in every caption is noise.

#### F3a — Spotify URL download (paste link anywhere, or Spotify state)
1. (new) "Processing ⏳" → (edit) "Fetching media info 🔎" → resolve URL (2–10 s).
2. Per track: (edit) "Downloading track 1/N 🎧" → download (10–60 s per track via yt-dlp + proxy) → (new) MP3 audio with album art, title, artist, duration, `protect_content` (forwarding blocked).
3. After all tracks: status deleted, (new) "All done 🥳" + [Back] [Home]. 👍
- **Flaws:** no way to cancel a 10-track playlist mid-flight; "Downloading track 2/10" has no ETA; on per-track failure the loop silently skips (user never sees which track failed); album art comes from Spotify CDN (can be slow to fetch through Telegram).

#### F3b — Spotify text search (type song name in Music states) — **the flow you most recently saw**
1. User types "starboy" in Music or Spotify state.
2. (new, ~2 s in) **Live status**: "🔎 Searching for **starboy** / ⏳ This can take a little while…" + [⏹️ Cancel] button. While the search runs (typ. **~45 s** through the proxy): typing indicator re-sent every 4 s, status text re-renders "⏳ 12s and counting…".
3. User taps **Cancel** → search wait is abandoned, status deleted, (new) "⏹️ Search cancelled. Send a new song name to start over." (The backend call still finishes in the background; result discarded.)
4. Search done → status (del) → **up to 3 track cards**, one photo message each: cover art + "🎵 **Title** / *Artist* / 📀 Album / ⏱ 3:34" + buttons:
   - Row 1: [⬇️ Download MP3] [🎧 30s Preview]
   - Row 2: [📄 Lyrics] [🔗 Open on Spotify]
5. ⬇️ Download → answer spinner, then the F3a download flow for that track.
6. 🎧 Preview → (new) 30 s audio clip (from Spotify's preview URL if present, else iTunes/Deezer lookup ~1 s; free backend has no preview_url so it's the iTunes path in practice). If nothing found: toast "No 30s preview found for this track".
7. 📄 Lyrics → toast "This tool is going to be available in a near future... ❤️" (Phase 2).
8. No results → (new) "No results for **x**. Try `artist title`…". Backend down → (new) "Couldn't reach Spotify right now. Try again in a moment."
9. Results are cached 300 s (same query re-answered instantly from cache); the candidate list is stored per-user in memory — **a process restart or a newer search invalidates old card buttons** → clicking a stale button: alert "Results expired — search again".
- **Flaws (user-tested, owner's words: "still flaws, users don't want to use it"):**
  - The 45 s wait is the whole experience. Feedback exists now but the *wait itself* is unexplained and unbroken — no "searching on Spotify… → matching tracks… → fetching covers…" phases, no partial results, no suggestion that inline mode is faster.
  - 3 separate photo messages for one query = chat noise; no "these are your 3 matches" grouping.
  - No way to **pick more** ("show 5", "search again with…"), no refinement ("I meant the Daft Punk one").
  - The cancel button appears under the status text — far from the thumb, and the status message scrolls away if the user scrolls.
  - Cards are only reachable from two hidden states (Music → Spotify). A user at the main menu with no link has **no visible search affordance at all** (no search field, no "type a song name" hint on the menu).

#### F4 — YouTube (menu path: YT → Video/MP3 → paste URL; or paste a YT link anywhere)
1. **Link pasted anywhere (incl. pre-/start):** instant (reply to the link message) "Pick a format for this video 👇" + [🎬 Video (720p)] [🎬 Video (360p)] / [🎵 Audio MP3]. Tapping a format starts the download; tapping a **stale** button (newer link pasted meanwhile, or restart): toast "Link expired — paste it again".
2. Download: (new) "Processing ⏳" → (edit) "Fetching media info 🔎" (5–15 s) → (edit) "Downloading and processing video ⬇️" (10 s–5 min depending on length/playlist) →
3a. Single file < 50 MB: (del) status, (new) video (streaming, cover, caption with title) [Back] [Home], 👍.
3b. **File > 50 MB:** (del) status, (new) "The file is too big to send here. You can download it using this link: `<url>`" + [📋 Copy link] button + [Back] [Home]. (The URL points at the server's public file host via a tunnel.)
3c. Playlist: same flow per item — status deleted **once** before the loop, then N video messages (no per-item "3/12" progress for video; MP3 playlist has none either).
4. Errors: unextractable link → 👎 + status edited to "Something went wrong 🤯". Non-YouTube URL in YT state → "That link looks invalid…"
- **Flaws:** the format picker is the best part of the bot — but it's invisible in the menu flow (menu path = type URL in a state that says "send me the url", and the picker never appears for menu-path users — **menu-path YT links download 720p blind**; only the dispatcher path shows the picker). Playlist = silent multi-minute operation, no cancel, no progress count for video. >50 MB link depends on a tunnel URL (flaky by nature) with no "link expires in…" note. **Bug:** in the YOUTUBE_DOWNLOAD state the [Back] button currently navigates to **Instagram** (copy-paste bug, §6.4).

#### F5 — Pinterest (Pin button → paste pin.it URL)
Same shape as F4 without the format picker: Processing → info → "Downloading and processing video ⬇️" → (new) video or, if not a pin.it URL, "That url is invalid. It should start with https://pin.it/". No progress count, no cancel.

#### F6 — TikTok (paste link anywhere)
Routed to the video downloader (720p blind, watermark not removed). No format picker, no special feedback.

#### F7 — Universal link dispatcher (the zero-friction layer)
- Works in every state and pre-/start. Spotify/Instagram/Pinterest links start downloading immediately (no confirm, no format choice); YouTube gets the picker; unknown-but-known-platform (tiktok) downloads; a non-media URL in a state with a text handler goes to that state (e.g. a Google link typed in AI chat → sent to the LLM).
- **Flaws:** no confirmation before long downloads (paste a 40-min playlist link → 10 minutes of downloading before the user notices); no "what will happen" preview; dispatcher + menu path can both fire for the same link in some states (the in-conversation copy is guarded, but the *semantics* — "same link, two different behaviors depending on where you are" — is confusing by design).

#### F8 — Inline query (`@harzad_bot <song>`)
1. User types `@harzad_bot starboy` in any chat/group. Telegram shows native inline results (client-side spinner — free feedback).
2. 3 articles: cover thumbnail + title + "artist • album". Tapping → the track card (same as F3b) is delivered **into that chat**.
3. Cache 300 s. Empty query <2 chars or backend failure → empty answer (nothing shown).
- **Flaws:** in a group, only the searcher's buttons work; any other member tapping Download/Preview gets "Results expired — search again" (confusing, feels broken). No inline support for anything but songs (no "youtube video", no media by link). No "share this song" affordance on in-chat cards (mirror image of the same gap).

#### F9 — AI chat (AI → Chat)
1. Prompt: "Start a conversation with me 🙂".
2. Type anything → 5–10 s wait (local LLM, one-shot, **no conversation memory between messages** — each message is answered in isolation) → (new) plain-text answer + [Back] [Home].
3. A pasted media link is *not* sent to the LLM (dispatcher intercepts first).
- **Flaws:** no streaming (full 5–10 s silence after typing), no stop button, no memory (user cannot follow up — "and what about its sequel?" has no context), no indication this is a small local model (users expect GPT-class answers), no per-topic guardrails.

#### F10 — AI text-to-image (AI → Text to image 🖼)
**Dead button.** The TTI navigation target is not wired (button answers with nothing, state never changes) and the image engine's SDK is not installed. The instruction screen and handler exist in code but are unreachable/broken. Either design this flow for real (engine TBD) or **remove the button from the AI menu** — a dead button in production is worse than no button.

#### F11 — Mini App ("Open Harzad")
A full HTML web app (dark theme, accent #2481cc), two tabs:
- **Download**: URL input → creates a "job" (uuid) → job status polling → file delivered as a download link (works around 50 MB limit for the user's own device; no chat delivery).
- **Chat**: AI chat (same backend).
Auth: Telegram WebApp initData (HMAC-verified); anonymous browser access = degraded mode.
- **Flaws:** the Mini App and the chat bot are **two separate products with no shared language** — the job model, the URL field, the styling; no music/search tab (planned for a later phase: visual search with cover grid + in-app player), no onboarding, no feedback on job failures beyond text.

#### F12 — Errors, edges, and system states
| Situation | What the user sees today |
|---|---|
| Any unhandled exception | 👎 reaction on their message + "Something went wrong 🤯 Please try again." (no detail, no retry button) |
| DB (logging) outage | Nothing — features keep working (circuit breaker), logging silently skipped. **Good — keep.** |
| Spotify backend down | "Couldn't reach Spotify right now. Try again in a moment." (search) / generic error (URL download) |
| Instagram session expired server-side | "Something went wrong 🤯" (misleading — it's a server-side credential problem, not the user's fault) |
| Stale song-card button | Alert "Results expired — search again" |
| Stale YT format button | Toast "Link expired — paste it again" |
| Stale search Cancel button | Toast "That search already finished" |
| Process restart | All conversation states, candidates, pending tokens gone. User's next message behaves as if they never started (links still work; menu resets). No notification. |
| Second request while one runs | Both run; messages interleave (no queue, no "busy" state) |
| Non-media text in a state expecting a link | "That link looks invalid…" / "Media not found…" (mixed copy quality) |
| Group chat, button from another user's card | "Results expired" (see F8) |

---

## 5. Latency & Performance Profile (measured, 2026-10-27, through the production proxy)

| Operation | Typical | Worst observed | User sees |
|---|---|---|---|
| /start → menu | 1–4 s | 6 s (DB pool timeout) | 5 s typing, then menu |
| Menu button tap → screen | 0.3–2 s | 4 s | button spinner |
| Spotify **text search** (3 tracks) | **~45 s** | 60 s+ | live status (new) |
| Spotify URL resolve | 2–10 s | 20 s | "Fetching media info" |
| Spotify **track download** | 10–60 s | 3 min (bad proxy) | "Downloading track N/M" |
| 30 s preview lookup (iTunes) | ~1 s | 5 s | none (toast if missing) |
| YouTube extract info | 5–15 s | 30 s | "Fetching media info" |
| YouTube video download (3 min video, 720p) | 20–90 s | 5 min | "Downloading and processing video" |
| Instagram fetch | 3–15 s | 30 s | "Fetching media info" |
| AI chat answer | 5–10 s | 20 s | typing, then full answer |
| DB user sync (per message) | 0.1–0.5 s | 3 s (pool timeout) | nothing (before first reply) |

**Design rules these numbers force:**
- Any operation > 3 s **must** have: instant acknowledgment (< 1 s), a visible ongoing indicator that survives > 5 s, and (if > 30 s) cancellation or a meaningful next step.
- The Spotify search is the flagship slow path. 45 s is ~10× too slow for "instant" — the design must either *make it bearable* (phases, partial results, parallelism) or *route users around it* (e.g. suggest inline mode, which shows Telegram's native spinner and can run while the user keeps using the bot).
- Two DB writes happen before the first reply in most flows. The design should not assume sub-second first responses; budget 1–4 s.

---

## 6. Known UX Flaws (ranked by owner impact)

**P0 — users leave because of these**
1. **Search feels broken.** 45 s of "searching", no explanation of *why* it's slow, no partial progress, no alternative path. (Feedback UI exists now — status + cancel + elapsed — but the owner still calls it not good enough. Redesign the whole wait: phases, expectations, parallelism, or a faster path.)
2. **No global search / discovery entry.** The single most useful modern feature (type any song anywhere) is hidden behind Music → Spotify. The main menu offers zero way to search by name.
3. **Long downloads can't be cancelled** (Spotify playlists, YouTube playlists/videos). User who fat-fingered a 50-track playlist is hostage for 30 minutes.
4. **No task awareness.** No "what's downloading / what finished / what failed" — results and errors just stream into the chat. No queue, no history, no retry affordance.

**P1 — users notice and get confused**
5. **State is invisible.** User doesn't know which mode they're in (menu says "Choose an option 👇" in 5 different screens). One wrong tap and they're in a different branch with no map.
6. **Menu depth + missing affordances.** 3 taps to Spotify-URL entry; no search field; "choose an option" on screens with one option; Back button in YT-download state goes to **Instagram** (bug — fix regardless of design).
7. **Dead TTI button** in the AI menu (answers, goes nowhere, engine missing). Remove or make real.
8. **Generic, scary errors.** "Something went wrong 🤯" + 👎 for everything from proxy hiccups to dead server credentials. No cause, no suggested action, no retry button.
9. **Group-mode button ownership.** Cards shared in groups are dead for everyone but the searcher ("Results expired" to other members).
10. **AI chat has no memory, no streaming, no stop.** Feels dumb next to Telegram's own @GigaChat/GPT bots.

**P2 — polish**
11. **Restart amnesia with no signal.** Cards expire silently after a deploy; users blame the bot.
12. **Chat noise.** 3 cards = 3 messages; IG album = N messages; no grouping/counters.
13. **No onboarding.** First screen shows the user's own numeric ID (why?). No "try me with this example" affordance.
14. **Bot ID in every media caption** ("🆔 @harzad_bot") — brand noise on 100% of delivered media.
15. **>50 MB fallback link** has no expiry/validity note; depends on tunnel uptime.
16. **No localization** (English only; Persian audience).
17. **Protect-content on everything** — users can't forward received media to friends (sharing is a core job for a music bot; deliberate? confirm with owner).

---

## 7. Platform Constraints — Design Inside These Walls

Hard limits of the **Telegram Bot API** (Bot API 10.0, python-telegram-bot 22.8). Not negotiable:

**Messages**
- Text: 4,096 chars. Media caption: 4,096 chars (HTML parse mode supported).
- A bot **edits** its own messages in place (the current pattern — keep it, it's what keeps chats clean). It cannot delete *user* messages (except in some group cases) and cannot edit messages older than 48 h in some cases.
- No progress bars, no sliders, no custom widgets in chat. Progress = text/emoji edits, typing indicator (lasts ~5 s, must be re-sent to keep alive), upload indicators (sendChatAction: typing/upload_photo/upload_video/upload_audio — automatic while the bot uploads).
- No way to "disable" an inline button after sending; you can only replace the whole keyboard (by editing) or let stale clicks be answered with a toast.
- **Reactions** (emoji) can be set on any message — currently used as success/fail signal (👍/👎). Good channel for non-intrusive feedback.
- `protect_content=True` can be toggled per message (forwarding lock) — a design decision per asset type.

**Inline keyboards (the only interactive element in chat)**
- 1–100 buttons total, 8 rows max practical; 1–2 buttons per row is the norm here.
- `callback_data`: **max 64 bytes** — buttons can carry an ID, not data. (Current scheme: `nav:<screen>`, `song:<action>:<index>`, `yt:fmt:<res>`, `srx:c:<8-hex>`.)
- One tap = one `answerCallbackQuery`: optional **toast ≤ 64 chars** (shown above the input bar) or an **alert** (modal, needs OK). Decide per action: toast for "done/ack", alert only for "you must read this".
- Buttons render emoji + text only. No icons, no images in buttons (button styles: default / "primary" blue — currently used for action buttons).
- URL buttons (no handler) and copy-text buttons (📋 Copy link) exist and are used.

**Callbacks & toasts**
- A tap that isn't answered ≤ 10 s leaves the client spinner spinning forever — **every** button, in **every** state, must be answered (the current architecture has a catch-all for exactly this; keep the property).

**Inline mode (E5)**
- Results: article (title ≤ 64, description ≤ 256, one thumbnail, arbitrary reply markup) or audio (needs direct URL — only viable for 30 s previews). Answer cache: 0–3600 s (currently 300).
- Results are per-query, anonymous: the *searcher* is the only one whose buttons can be personalized (per-user server state).

**Files**
- Bot upload cap **50 MB** (Bot API limit; a self-hosted bot-api server could raise it to 2 GB — §8). Video ≤ 50 MB can be sent *streaming* with cover + duration + caption.
- Bot download cap 20 MB (incoming user media — relevant if any future "send me the voice note to identify" feature).
- Telegram fetches remote URLs server-side (cover art, preview MP3s, IG media) — slow/broken CDN = bot can't show it, even if the URL works in a browser. Design must have text fallbacks (already the case for covers — keep the property).

**Mini App (the escape hatch)**
- A real HTML/JS/CSS app inside Telegram: full design freedom (grids, players, forms, haptics). Entry: chat menu button + deep link. Auth via initData. If the design needs anything the chat API can't express (visual search grid, audio player, history view, queue UI), **that's where it lives**. Note: the Mini App and chat are separate surfaces — the design must state which surface owns which flow and how the user moves between them.

**Bot architecture constraints (implementation side — affects what's feasible)**
- One conversation per user at a time (a menu state machine). Adding a parallel "mode" (e.g. search) = a new state or a new entry path — cheap to medium.
- Per-user in-memory data (no persistence): anything "remembered" (favorites, history, queue) survives only until the next deploy unless we add persistence (medium cost).
- Operations run **concurrently** (no queue today). A designed task queue / "job list" = medium cost (in-memory is fine).
- All egress is through one flaky proxy — every extra API call is ~0.3–2 s. Prefer in-place edits over new messages, batch where possible.

## 8. Implementation Feasibility Guide (cheap → expensive)

| Cost | Changes |
|---|---|
| **Cheap** (hours) | All copy; all keyboard layouts/order/labels; new pattern-matched callback buttons (e.g. "show more", "retry", "cancel download" if the underlying op is awaitable); reordering states; adding a search field *hint* to menus; reactions; status-message wording/timings; removing dead features; per-flow error copy + retry buttons; toasts vs alerts policy |
| **Medium** (1–3 days each) | New conversation state or entry path (e.g. "search from anywhere" via a menu search screen); task queue + job list UI (in-memory jobs, status messages); per-job cancel tokens for downloads (same mechanism as search-cancel exists already — extending it to downloads is mechanical); partial results for search (stream top-1 then top-3); group-safe card buttons (per-chat token map); mini-app new tab/section; persistence (SQLite/PG) for favorites/history/survive-restart; bilingual copy |
| **Expensive** (week+) | New engines: audio identification (Shazam), synced lyrics provider, YouTube summarizer; local `telegram-bot-api` server (2 GB uploads, kills the >50 MB problem entirely); LLM streaming to Telegram (draft-message/throttled-edit); full mini-app media hub (player, grid, queue) |

**Use this to set P0/P1/P2.** The owner wants users to actually use the bot — bias P0 toward: making job 1 (link → file) and job 2 (name → MP3) fast, legible, and cancellable; a visible search entry; honest errors.

---

## 9. Design Brief — What You Deliver Back

**Scope:** the whole bot (all of §4, flows F1–F12 + mini app). Not just music.

**Deliverable: one implementation-ready spec** (Markdown, one section per flow, plus the global sections below). Engineering implements it **verbatim** — so precision matters more than pretty pictures. (Figma/screens optional; the *spec* is the contract.)

For **every flow**, specify:
1. **Goal & entry points** (from menu? from anywhere? inline? mini app?).
2. **Screen-by-screen sequence** — for each step: *user does X → bot shows Y*. Y must be the **exact user-facing text** (HTML tags allowed, same style as Appendix A) + **exact buttons** (label, row layout, primary vs neutral, callback ID pattern for new buttons within the 64-byte budget).
3. **Every transient state of every slow operation** — what shows at 0 s / 5 s / 30 s / 60 s / timeout. (Use §5 numbers. If you want a new operation, give its expected duration.)
4. **Cancel/interrupt behavior** for every operation > 10 s: what the user can tap, what happens to in-flight work, what the user sees afterward.
5. **Success state**: exact message (or "nothing, just the file"), reaction, cleanup (which status messages die).
6. **Every error/edge case**: trigger → exact user-facing message + suggested action (retry button? re-send link hint?). Map each to a row in §4.3-F12's table or add new rows.
7. **Stale/expired behavior** for every button that carries state (card buttons, format picker, cancel).

**Global sections:**
- **G1. Navigation model** — how the user finds their way: home/back semantics, the state indicator (what shows *where the user is*, and where it lives — e.g. menu title line, caption, or mini app only), and whether the menu stays a tree, becomes a flat list, or gets a search-first layout.
- **G2. The wait design system** — one consistent pattern for all slow operations (status message shape, phases, counter, cancel placement, what dies when done). Currently search has one and downloads have another; unify.
- **G3. Error design system** — taxonomy (retryable / user-fixable / server-side), tone, and the exact message + action button for each class. Kill "Something went wrong 🤯" as a catch-all if you can (keep one final fallback only).
- **G4. Group-chat spec** — what works in groups, what's private-chat-only, and how shared cards behave for non-searchers (design the ownership story).
- **G5. Mini App spec** — tab structure, which flows it owns vs the chat, shared visual language, and the handoff points (open in chat / share result to chat).
- **G6. Copy deck** — every user-facing string in one table (key, English text, Persian if you localize), including toasts (≤ 64 chars!) and alerts.
- **G7. Priorities** — P0 / P1 / P2 on every change, with one-line rationale (use §6 + §8).
- **G8. Open questions** — anything needing the owner's call (e.g. protect_content policy, TTI: real or cut, localization scope, 2 GB file server).

**Definition of done for your spec (engineering will check these before building):**
- [ ] Every existing button in Appendix A appears in your spec: kept (same behavior), changed (new behavior stated), or removed (reason).
- [ ] No dead ends: every state has an exit (back/home/re-navigate).
- [ ] Every slow operation has the §9.3 transient table.
- [ ] Every error has a user-visible cause + next action.
- [ ] New callback IDs listed with patterns (≤ 64 bytes) and stale-click behavior.
- [ ] P0 flows complete end-to-end including first-use (no prior menu knowledge).

---

## 10. Success Criteria (how the redesign will be judged)

1. **Job 1** (paste link → file): ≤ 1 s to visible acknowledgment, no user decision required for single-media links, cancel available, file or exact reason within the time the engine actually needs.
2. **Job 2** (name → MP3): search entry reachable from the main menu in **1 tap**, user informed of expected wait *before* it starts, partial results if possible, cancel in reach, result → MP3 in ≤ 2 taps.
3. **Zero dead buttons / zero silent waits** anywhere in the product (the two things that currently make users feel the bot is broken).
4. **Errors are actionable**: no error message without a next step.
5. **First-time user** completes a download within 2 minutes without reading /help.
6. **Group-safe**: a group member never sees another user's private state, and shared cards either work for them or say clearly why not.

---

## Appendix A — Current Inventory (reference for G1/G2/G3/G6 and the "kept/changed/removed" audit)

### A.1 Conversation states (12)
`START(0) · INSTAGRAM(1) · INSTAGRAM_DOWNLOAD(2) · MUSIC(3) · MUSIC_SPOTIFY(4) · PINTEREST(5) · YOUTUBE(6) · YOUTUBE_DOWNLOAD(7) · YOUTUBE_MP3(8) · AI(9) · AI_CHAT(10) · AI_TTI(11, unreachable)`

### A.2 Callback IDs
| ID | Button (label) | Where | Behavior |
|---|---|---|---|
| `nav:start` | Home 🏘 (or menu) | every keyboard | → START, re-send greeting + menu |
| `nav:ig` | Instagram | START | → INSTAGRAM screen (edit in place) |
| `nav:ig:dl` | Download | INSTAGRAM | → INSTAGRAM_DOWNLOAD prompt |
| `nav:music` | Music 🎵 | START | → MUSIC screen |
| `nav:music:spotify` | Spotify | MUSIC | → MUSIC_SPOTIFY prompt |
| `nav:pin` | Pinterest | START | → PINTEREST prompt (it's the input state) |
| `nav:yt` | Youtube 🎞 | START | → YOUTUBE screen |
| `nav:yt:dl` | Download as video | YOUTUBE | → YOUTUBE_DOWNLOAD prompt |
| `nav:yt:mp3` | Download as mp3 | YOUTUBE | → YOUTUBE_MP3 prompt |
| `nav:ai` | AI 🤖 | START | → AI screen |
| `nav:ai:chat` | Chat 💬 | AI | → AI_CHAT prompt |
| `nav:ai:ttI` | Text to image 🖼 | AI | **dead — no navigation target** |
| `yt:fmt:720` / `yt:fmt:360` / `yt:fmt:mp3` | 🎬 Video (720p/360p) / 🎵 Audio MP3 | format picker (after any pasted YT link) | start download of stashed URL; stale → toast |
| `song:dl:<i>` | ⬇️ Download MP3 | track cards | download candidate i |
| `song:prev:<i>` | 🎧 30s Preview | track cards | 30 s clip |
| `song:lyr:<i>` | 📄 Lyrics | track cards | "coming soon" toast |
| (URL button, no ID) | 🔗 Open on Spotify | track cards | opens Spotify app/web |
| `srx:c:<8-hex>` | ⏹️ Cancel | live search status | cancels the in-flight search; stale → toast |
| (Back 🔙) | per-state back | most keyboards | → parent screen (YT-download: **bug → Instagram**) |

### A.3 Keyboards (button maps)
| Keyboard | Rows |
|---|---|
| `start` | [Instagram] / [Music 🎵] / [Pinterest] / [Youtube 🎞] / [AI 🤖] |
| `instagram_state` | [Download] / [Back 🔙] [Home 🏘] |
| `instagram_download` / `pinterest` | [Back] [Home] |
| `music_state` | [Spotify] / [Back] [Home] |
| `music_spotify` / `youtube_download` / `youtube_mp3` / `ai_chat` / `ai_tti` | [Back] [Home] |
| `youtube_state` | [Download as video] [Download as mp3] / [Back] [Home] |
| `ai_state` | [Chat 💬] [Text to image 🖼] / [Back] [Home] |
| `youtube_format` | [🎬 Video (720p)] [🎬 Video (360p)] / [🎵 Audio MP3] |
| `track` (per card) | [⬇️ Download MP3] [🎧 30s Preview] / [📄 Lyrics] [🔗 Open on Spotify] |
| `search_cancel` (status msg) | [⏹️ Cancel] |
| `direct_link` (>50 MB) | state back/home + [📋 Copy link] |

Button style rule today: **primary blue** = the action the user is there for; neutral = Back/Home. Keep or change deliberately.

### A.4 Message templates (current, English, HTML)
| Key | Text |
|---|---|
| HELLO | "Hi, **{name}** 👋 / Your id: `{user_id}` / Pick an option below to get started 👇" |
| HELP | per-platform one-liners + "Send `/start` any time to open the menu again." |
| PROCESSING | "Processing ⏳" |
| GETTING_MEDIA_INFORMATION | "Fetching media info 🔎" |
| GETTING_STORY_INFORMATION | "Getting Story information …" |
| DOWNLOADING_VIDEO | "Downloading and processing video ⬇️" |
| SENDING_THUMBNAIL / SENDING_VIDEO | "Sending thumbnail …" / "Sending Video …" |
| STARTING_DOWNLOAD / UPLOAD_IN_TELEGRAM | "Starting Download ..." / "Upload In Telegram ..." (legacy, partly unused) |
| SPOTIFY_INSTRUCTION | "Please send me the url of the song, album or playlist you want to download. / Sometimes some songs in a playlist do not have a public link to download, so I can't download them for you, sorry for that." |
| SPOTIFY_DOWNLOAD_PROGRESS | "Downloading track {current}/{total} 🎧" |
| INSTAGRAM_INSTRUCTION | "Please send me the url of the post, story or reel you want to download. / You can also send a username in the format `@username` to download their profile picture." |
| YOUTUBE_INSTRUCTION | "Please send me the url of the video or playlist you want to download. / Sometimes some videos are not downloadable, so I can't download them for you, sorry for that." |
| PINTEREST_INSTRUCTION | "Please send me the url of the Pinterest video you want to download." |
| AI_CHAT_INSTRUCTION | "Start a conversation with me 🙂" |
| AI_TTI_INSTRUCTION | "Send me a prompt and I will generate an image from the text. / For example `an iguana on the beach`, or a long, detailed prompt." |
| AI_PROMPT_FOR_CAPTION | "Prompt: `{prompt}`" |
| CHOOSE_MESSAGE | "Choose an option 👇" |
| MEDIA_CAPTION | "{caption} / 🆔 @harzad_bot" |
| MEDIA_NOT_FOUND | "Media not found or unavailable. Please check your link and try again." |
| LINK_IS_INVALID | "That link looks invalid. Please check it and try again." |
| INVALID_PINTEREST_URL | "That url is invalid. It should start with `https://pin.it/`" |
| DIRECT_VIDEO_LINK | "The file is too big to send here. You can download it using this link: / `{path}`" |
| YT_FORMAT_PROMPT | "Pick a format for this video 👇" |
| UNSUPPORTED_LINK | "I don't know how to download from `{domain}` yet. / I handle **Spotify**, **YouTube**, **Instagram**, **Pinterest** and **TikTok** links." |
| TASK_DONE | "All done 🥳" |
| SOMETHING_WENT_WRONG | "Something went wrong 🤯 Please try again." |
| COMMING_SOON | "This tool is going to be available in a near future... ❤️" |
| TRACK_CARD | "🎵 **{title}** / *{artist}* / 📀 {album} / ⏱ {duration}" |
| SEARCHING | "🔎 Searching for **{query}** / ⏳ *This can take a little while…*" |
| SEARCHING_TICK | "🔎 Searching for **{query}** / ⏳ *{elapsed}s and counting…*" |
| SEARCH_CANCELLED | "⏹️ Search cancelled. Send a new song name to start over." |
| NO_RESULTS_FOUND | "No results for **{query}**. / Try `artist title`, e.g. `Daft Punk Get Lucky`." |
| SEARCH_FAILED | "Couldn't reach Spotify right now. Try again in a moment." |
| TOASTS (≤ 64 chars) | "Results expired — search again" · "No 30s preview found for this track" · "That search already finished" · "Link expired — paste it again" |

### A.5 Bot profile
- Name: **Harzad** · @harzad_bot
- Description: "Harzad downloads media from Instagram, Spotify, Pinterest and YouTube, and can chat or generate images with AI. Send /start to begin."
- Short: "Media downloader + AI 🎧🎞🤖"
- Commands: `/start` (Open the menu), `/help` (How to use the bot)
- Chat menu button: "Open Harzad" → Mini App (when configured)
- Reactions as feedback: 👍 success / 👎 failure (on the user's triggering message)

### A.6 Roadmap context (owner's plan — design may reference it)
- **Phase 1 (done, being UX-hardened now):** zero-friction link ingestion, Spotify text search + cards + previews, inline query, storage GC, DB soft-failure.
- **Phase 2 (planned):** more platforms (Twitter/SoundCloud), **audio identification (Shazam)** — "send a voice note, get the song", YouTube video summarizer (AI), **synced lyrics** (fills the 📄 button).
- **Phase 3 (planned):** 2 GB file delivery (local bot-api server — makes the >50 MB problem disappear), **Mini App media hub** (visual search grid, in-app audio player with queue), **AI streaming**, deployment hardening.
- Design P0s for Phase 1 UX *now*; structure the design so Phase 2/3 slots in without rework (e.g. where would "identify this audio" live in the menu? where does the lyrics button's future real content land?).

---

*End of handoff. Questions → engineering. Bring back the spec per §9; implementation starts on receipt.*
