# Harzad — UX Redesign Specification

**Version:** 1.0  
**Date:** 2026-10-27  
**Status:** Proposed implementation contract  
**Source:** `PRODUCT.md` (AS-IS product handoff, measured latency, Telegram constraints, and implementation-cost guide)

## 0. Executive decision

Harzad should become a **link-first utility with one-tap music search and explicit task control**. Do not redesign it as a deeper menu tree, and do not try to make the Telegram chat imitate a full web application.

The redesign separates three things that are currently conflated:

1. **Navigation:** the screen the user is currently exploring.
2. **Input mode:** what plain text means when the user sends it (for example, a song query in Search Music or a question in AI Chat).
3. **Tasks:** searches, downloads, and AI requests already running. Tasks continue when the user navigates elsewhere; navigating Home does not silently cancel work.

The universal link dispatcher remains the highest-priority input rule. A supported link works from any state and before `/start`; it is dispatched exactly once. A YouTube link always opens the same format picker, regardless of how the user reached the bot.

### Release priorities

- **P0 — ship first:** Home/search redesign; consistent link dispatch; honest task statuses; cancellation; task awareness; actionable errors; stale callback behavior; remove the dead image-generation option; fix YouTube Back; group-safe shared music cards; remove noisy user ID and bot ID from media captions; remove unnecessary result-message spam.
- **P1 — next:** Persian localization; saved/recent task history; more search results and query refinement; Mini App Music/Tasks sections; AI conversation memory/streaming if technically supported.
- **P2 — roadmap:** visual music discovery/player queue, lyrics, text-to-image after a working engine exists, audio identification, 2 GB file delivery, richer AI streaming.

Do not claim that status-copy changes reduce the actual 45-second search latency. The redesign makes the wait comprehensible; engineering work is still needed to reduce the underlying wait.

---

## 1. Experience principles and invariants

1. **Links work everywhere.** In a private chat, a supported link is dispatched before any state-specific text handler. In group chats, processing follows Telegram privacy/mention rules; use inline mode for music discovery.
2. **A link has one meaning.** Every YouTube link opens the format picker (720p, 360p, MP3). A menu state must not silently change the format.
3. **A task is not a screen.** Back, Home, `/start`, and opening Tasks do not cancel active work. Only a cancel action cancels a task.
4. **Acknowledge quickly.** Engineering must send an acknowledgement/status message before non-critical user-sync/logging writes block the response path. Target: visible acknowledgement in under 1 second. The current 1–4 second `/start` delay is not an acceptable design dependency.
5. **No fake progress.** Show real phases, item counts, measured elapsed time, or truthful “still working” feedback. Never invent a percentage or ETA when the engine cannot provide one.
6. **One status message per task.** Edit it as the task changes phase; do not send a new “processing” message every few seconds. Remove it when a single-item task succeeds; retain a final playlist summary when it adds value.
7. **Every slow task can be cancelled.** For work over 10 seconds, provide an obvious Cancel button. Cancellation is best-effort for an operation already being uploaded to Telegram; communicate that distinction honestly.
8. **Every error has a next action.** Retry, edit/re-send the input, search again, or understand that the service is temporarily unavailable.
9. **Never punish users for server failures.** Instagram session expiry, proxy failure, and internal errors are described as service-side issues, not bad links.
10. **No dead buttons.** If a capability is not implemented, remove its visible entry point. Do not show “coming soon” actions that appear tappable.
11. **Keep chat readable.** Prefer edits and grouped result presentation. Do not add brand tags to every delivered file.
12. **Preserve user control and group safety.** A shared card must either work for any person who taps it or clearly state why it cannot. It must never depend on the original searcher's private in-memory state.

## 2. Information architecture and navigation (G1)

### 2.1 Home

Replace the current five-button tree with a small action-first Home. The primary call to action is music search; download-by-link needs no menu button because users can paste a supported link at any time.

**HOME message (exact):**

> Hi, {first_name} 👋\nSend any supported media link here to download it, or search for a song by name.\nYou can paste a link anytime — no setup needed.

**Keyboard (exact row layout):**

| Row | Buttons | Style |
|---|---|---|
| 1 | `🔎 Search music` | Primary |
| 2 | `📷 Instagram` · `▶️ YouTube` | Neutral |
| 3 | `📌 Pinterest` · `🤖 AI chat` | Neutral |
| 4 | `📥 Tasks` · `❓ Help` | Neutral |

Do not include the numeric user ID. Do not include Spotify as a separate submenu: a Spotify URL can be pasted anywhere, and song search has its own visible entry point.

### 2.2 Input screens

These screens explain accepted input and provide exits. Each screen has `🏠 Home`; input screens also have `📥 Tasks` where space is useful. Do not repeat “Choose an option” when the user needs to send text.

**Search Music prompt:**

> Type a song title, artist, or both.\nExample: `Daft Punk Get Lucky`\nYou can also paste a Spotify link anytime.

Buttons: `[🏠 Home] [📥 Tasks]`

**Instagram prompt:**

> Send an Instagram post, reel, or story link.\nFor a profile picture, send a username like `@username`.

Buttons: `[🏠 Home] [📥 Tasks]`

**YouTube prompt:**

> Send a YouTube video or playlist link.\nI’ll ask you to choose 720p, 360p, or MP3.

Buttons: `[🏠 Home] [📥 Tasks]`

**Pinterest prompt:**

> Send a Pinterest video link.

Buttons: `[🏠 Home] [📥 Tasks]`

**AI Chat prompt:**

> Ask me a question. I answer each message independently for now, so include any context I need in each message.

Buttons: `[🏠 Home]`

### 2.3 Navigation semantics

- `/start`: always send a fresh Home message. Do not cancel tasks.
- Home button: edit the current navigation/result message to Home when possible; otherwise send Home. Do not touch status messages for active tasks.
- Back: return to the logical parent screen using a mapping, not a hard-coded copied callback. If the parent is unavailable after a restart, go to Home.
- `/help`: show the Help message with Home, not a fresh unlabelled menu.
- Add `/cancel` and `/tasks` commands. `/cancel` cancels the sole active task; if several tasks are active, it opens Tasks with the active task list rather than guessing. `/tasks` opens Tasks.
- `/start`, `/help`, `/cancel`, and `/tasks` are accepted in every input mode.
- A pasted supported URL is dispatched before the screen's text handler. Do not dispatch the same update twice.
- Plain text is interpreted by the current input mode: query in Search Music, prompt in AI Chat, `@username` in Instagram. On screens that require a link, plain non-link text gets the exact input guidance and does not trigger a fake downloader error.
- If the process restarted and lost an input mode, plain text gets a safe Home/input hint; links still work through the stateless dispatcher.

### 2.4 Back-button mapping

| Current screen | Back destination |
|---|---|
| Search prompt/results | Home (results also have Search again) |
| Instagram input | Home |
| YouTube input/format picker | Home or dismiss picker; never Instagram |
| Pinterest input | Home |
| AI Chat | Home |
| Tasks | Home |
| Active task status | No Back button required; Home/Tasks navigation does not affect task |

Fix the existing `YOUTUBE_DOWNLOAD → Instagram` bug unconditionally.

### 2.5 Suggested state model

Keep the implementation's state machine as an input-routing detail, not a visible concept. Suggested logical modes: `HOME`, `SEARCH_INPUT`, `INSTAGRAM_INPUT`, `YOUTUBE_INPUT`, `PINTEREST_INPUT`, `AI_CHAT`. Retain compatibility aliases for existing state callback IDs during rollout. Track active tasks separately in a task registry. A task has: opaque token, owner user ID, origin chat ID/type, kind, input, status, timestamps, cancellable flag, per-item counts, result/error classification, and retry payload where safe.

Do not rely on process memory for durable history. In P0, the task registry may be in-memory as allowed by the current feasibility guide, but every callback must fail gracefully after restart. Persistence is P1.

---

## 3. Universal link dispatch

### 3.1 Routing policy

| Input | Behavior, regardless of current screen |
|---|---|
| YouTube video/playlist URL | Acknowledge immediately; show format picker. Never silently choose 720p. |
| Spotify track/album/playlist URL | Start resolve/download flow immediately. |
| Instagram post/reel/story/profile URL | Start Instagram flow immediately. |
| Instagram `@username` | Treat as profile-picture request only when Instagram input is active; elsewhere plain text follows the active input mode. |
| Pinterest URL | Start Pinterest flow immediately; validate the host/path and provide correct guidance on failure. |
| TikTok URL | Start TikTok flow and be honest that watermark removal is not supported today. |
| Unsupported URL | Do not send it to AI as though it were a question. Say which platforms are supported and provide a next action. |
| Plain text | Route to active input mode, or display Home/help guidance if no mode exists. |

In a group, do not claim the bot can observe arbitrary messages if Telegram privacy mode prevents it. Inline queries and explicit bot mentions remain supported entry paths.

### 3.2 YouTube format picker

**Message (exact):**

> Choose a format for this YouTube link.

**Keyboard:**

| Row | Buttons | Callback |
|---|---|---|
| 1 | `🎬 Video · 720p` · `🎬 Video · 360p` | `yt:fmt:<token>:720`, `yt:fmt:<token>:360` |
| 2 | `🎵 Audio · MP3` | `yt:fmt:<token>:mp3` |
| 3 | `✖ Cancel` | `yt:cancel:<token>` |

Use `primary` style for 720p only if the product owner explicitly wants to recommend it; otherwise keep all format choices neutral. The token identifies the exact URL, not “the latest URL pasted by this user.” Callback token length must keep the complete callback under 64 bytes.

On format selection, answer the callback immediately, create a task, and edit the picker message to the initial task status where possible. A new YouTube URL creates a new picker and never invalidates a different picker by replacing a single per-user URL slot.

Stale format selection: toast `This link expired. Send it again.` Do not start a download using a different link.

---

## 4. Global wait and task design system (G2)

### 4.1 Status message anatomy

One bot message per task:

- **What is happening:** resolve / search / download / upload / finish.
- **Context:** short title or input, plus `item N of M` when known.
- **Elapsed time:** show after 5 seconds; elapsed time must be measured, not simulated.
- **Action:** Cancel for every operation expected to exceed 10 seconds; Tasks when useful.
- **No invented ETA:** only show an ETA when the underlying engine provides a reliable estimate.

Acknowledgement target is <1 second. To meet it, send the first response before non-critical DB sync/logging; logging failures must not block user feedback.

### 4.2 Standard task progression

| Time/event | Exact message pattern | Keyboard/action |
|---|---|---|
| 0–1s, task accepted | `📥 Got it — checking your link…` (for search: search-specific copy below) | `⏹ Cancel` if the task is already cancellable; otherwise add it as soon as the task starts. |
| 5s, still resolving | `🔎 Getting media details…\n⏱ {elapsed}s elapsed. You can cancel anytime.` | `⏹ Cancel` · `📥 Tasks` |
| 30s, unresolved | `🔎 This is taking longer than usual. I’m still trying.\n⏱ {elapsed}s elapsed.` | `⏹ Cancel` · `📥 Tasks` |
| 60s, unresolved/long download | `⬇️ Still working on {short_title}.\n⏱ {elapsed}s elapsed. No reliable finish time is available yet.` | `⏹ Cancel` · `📥 Tasks` |
| Engine reports progress | `⬇️ Downloading {short_title}…\n{percent}%` only if real percentage exists; otherwise use stage + elapsed time. | `⏹ Cancel` · `📥 Tasks` |
| Upload starts | `📤 Sending {short_title} to Telegram…` | Keep Cancel only if the upload can actually be interrupted; otherwise use `📥 Tasks` and let Telegram's native upload indicator show activity. |
| Success, single item | Delete status; send the file. | No extra “All done” message. Add 👍 reaction to the triggering message when supported. |
| Success, playlist/batch | `✅ Finished: {success_count}/{total_count} files sent.` Add failed/cancelled count when non-zero. | `🔁 Retry failed` (if supported) · `📥 Tasks` · `🏠 Home` |
| User cancelled | `⏹️ Cancelled. {completed_count} file(s) already sent remain in the chat.` | `📥 Tasks` · `🏠 Home` |
| Timeout/failure | Use the error taxonomy in §7, not a generic processing failure. | Retry/input correction/Tasks as appropriate. |

The table is a design schedule, not permission to fake engine phases. If a phase is unavailable, repeat a truthful “still working” status with elapsed time and no invented sub-phase. Edit the status at meaningful thresholds; do not edit it every second.

### 4.3 Cancellation contract

- Every task has an opaque task token; `task:cancel:<token>` is independent of the navigation state.
- Acknowledge each button callback immediately so Telegram does not leave a spinner running.
- Cancel stops the current task and prevents new items in a playlist from starting. Already delivered files remain delivered.
- Kill the subprocess/request when the backend supports it. If cancellation is only best-effort, the UI must not say “stopped” until the worker confirms it; use `⏹️ Cancellation requested. I’ll stop as soon as the current step can be interrupted.` until confirmed.
- When a task is not cancellable anymore (for example, Telegram is already sending a file), replace the keyboard with Tasks/Home and do not leave an ineffective Cancel button.
- Cancel does not cancel another task. `/cancel` with multiple active tasks opens Tasks to let the user choose.
- A stale cancel callback replies with toast `This task has already finished.`

### 4.4 Proposed timeout guards

These are **recommended starting thresholds**, not measured production facts. Validate against production telemetry before enabling them as hard failures. Prefer the engine's own timeout and cancellation result when available.

| Operation | Proposed UX timeout guard | Timeout message/action |
|---|---:|---|
| Spotify text search | 90s | Search timeout copy + Search again + Home |
| Spotify URL resolve | 90s (raised from the proposed 45s on 2026-10-31: telemetry showed healthy resolves at 42.7–48.9s plus one 123.5s egress-stall outlier) | Temporary service error + Retry |
| Instagram metadata/fetch | 45s | Temporary Instagram service error + Retry |
| YouTube metadata/extract | 45s | Temporary YouTube service error + Retry |
| Video/audio download | 10 min per item, then validate by telemetry | Download timed out + Retry; for playlists, show completed items and failed item count |
| Pinterest/TikTok download | Use video engine guard, provisionally 10 min per item | Platform-specific retry message |
| AI answer | 30s | AI is taking too long + Retry/Ask again |
| 30s preview lookup | 10s | Preview unavailable + Search another track |

A timeout must cancel or detach the work in a defined way; never claim it stopped if the backend is still actively consuming resources. The task status must be marked `timed_out` or `cancel_requested` according to actual worker state.

---

## 5. Music search and track actions (F3b, F8)

### 5.1 Entry points

- Home → `🔎 Search music` (one tap).
- Legacy `nav:music` and `nav:music:spotify` buttons route to the same Search Music prompt.
- Plain text typed while in Search Music becomes a search query.
- A Spotify link pasted anywhere goes through the universal link downloader instead.
- Inline query `@harzad_bot <song>` remains supported in chats/groups.

### 5.2 Search wait and cancellation

Immediately after the user submits a query, create one status message:

> 🔎 Searching for <b>{query}</b>\nMusic search usually takes about 45 seconds. You can keep using Telegram while I search.

Keyboard: `[⏹ Cancel search]`

At 5 seconds, edit to:

> 🔎 Searching for <b>{query}</b>\n⏱ {elapsed}s elapsed. I’m still waiting for matches.

At 30 seconds, edit to:

> 🔎 Search is taking longer than usual for <b>{query}</b>.\n⏱ {elapsed}s elapsed. You can keep waiting or cancel.

At 60 seconds, edit to:

> 🔎 This search is slower than usual. I’ll show results if they arrive.\n⏱ {elapsed}s elapsed.

Keyboard remains `[⏹ Cancel search]`. When the proposed 90-second timeout is reached:

> I couldn’t finish that search in time. Try again, or include both the artist and song title.

Buttons: `[🔁 Search again] [🏠 Home]`

Do not say “Spotify is slow” unless the backend can identify Spotify as the source of the delay. Do not promise inline search is faster; it uses the same backend. You may say “You can use inline search in another chat and keep chatting while it runs” in Help, but not as a speed claim.

Cancel result:

> ⏹️ Search cancelled. Send another song name whenever you’re ready.

Buttons: `[🔎 Search another song] [🏠 Home]`

The current backend may finish after the user cancels. Discard its result and never send it after the cancel has been confirmed.

### 5.3 Results presentation: one editable result card

Do not send three separate photo messages for a single query. Show **one result card** and make it a small in-chat carousel by editing the same message's photo/caption and keyboard for previous/next candidates. If Telegram cannot edit a particular result's media, fall back to editing a text card in that same message where possible; send a new message only as a last resort.

**Result card caption (exact template):**

> 🎵 <b>{title}</b>\n<i>{artist}</i>\n📀 {album} · ⏱ {duration}\nMatch {index} of {total}

If album or duration is absent, omit that line component rather than displaying `None`, a blank label, or fake data. If cover art is unavailable, display the same metadata as a text message.

**Keyboard:**

| Row | Buttons | Callback pattern |
|---|---|---|
| 1 | `⬇️ Download MP3` · `🎧 30s Preview` | `song:dl:<token>:<index>` · `song:prev:<token>:<index>` |
| 2 | `◀ Previous` · `Next ▶` | `song:prevcard:<token>:<index>` · `song:next:<token>:<index>` |
| 3 | `🔎 Search again` · `🏠 Home` | `search:again:<token>` · `nav:home` |

At the first/last result, keep navigation buttons enabled and answer with a brief toast (`Already showing the first match.` / `That’s the last match.`), or disable the boundary button by replacing the keyboard. Do not leave a spinner running. Keep result state in a token map with expiry. Do not identify candidates only by mutable list index in per-user memory.

When more than one match is returned, index begins at 1. Cache the result set for 300 seconds as today. If the cache is invalidated, show a toast: `This result expired. Search for the song again.` and do not run an unintended action.

### 5.4 Search outcomes

**No results:**

> No matches found for <b>{query}</b>.\nTry the artist and title together, for example `Daft Punk Get Lucky`.

Buttons: `[🔁 Search again] [🏠 Home]`

**Backend unavailable:**

> Music search is temporarily unavailable on our side. Your query is fine — please try again shortly.

Buttons: `[🔁 Retry search] [🏠 Home]`

**Search retry:** reuses the query and creates a new task token. It must not silently submit multiple copies of the same request.

### 5.5 Track actions

**Download MP3:** acknowledge callback immediately; create a separate download task; do not replace the result card with a status message. Use the standard wait design. On success, send audio with title, artist, duration, and album artwork when available. Do not send an extra “All done” message for a single track.

**30s Preview:** use Spotify preview URL when present, otherwise the existing iTunes/Deezer lookup. At 0–1s respond to the callback; for a lookup under 5s, no status message is necessary unless the wait exceeds that threshold. If a preview is found, send the preview audio and keep the result card. If not found, toast `No 30-second preview is available.`

**Lyrics:** remove the button until a working lyrics provider exists. Do not keep a visible “coming soon” toast.

**Open on Spotify:** keep as a URL button. If the URL is unavailable, omit the button for that card rather than presenting a dead URL.

### 5.6 Inline search (group-safe)

- Keep the native inline query entry `@harzad_bot <song>`.
- Titles remain ≤64 characters and descriptions ≤256 characters.
- If query is too short, return one helpful inline result/help card instead of a silent empty result: title `Search for a song`; description `Type an artist or song title after @harzad_bot.`
- If the backend fails, return an inline help/error result when Telegram permits; do not silently provide nothing if a useful explanation can be shown.
- The inline result's action payload must identify an immutable, short-lived track token that works for **any user who taps the shared result**, not only for the searcher. It must not depend on the searcher's candidate list.
- For a button tapped in a group, the media will be sent to that group. Label the button `⬇️ Send MP3 here` in inline group cards where feasible so the destination is clear. In private inline contexts, `⬇️ Get MP3` is acceptable.
- Set `protect_content` according to the global owner policy in §11; do not silently differ between a private result and an inline result.
- If a shared token expires, toast `This card expired. Search for the track again.` The card must never show another user's private state.

---

## 6. Media download flows

### 6.1 Spotify URL (F3a)

**Entry:** Spotify track/album/playlist URL pasted anywhere; legacy Spotify input accepts either a URL or a song query.

**Start status:**

> 📥 Got it — checking this Spotify link…

After resolve:
- Single track: edit status to `⬇️ Downloading {title}…\n⏱ {elapsed}s elapsed.` with Cancel if cancellable.
- Album/playlist: `⬇️ Downloading track {current}/{total}: {title}\n⏱ {elapsed}s elapsed.` with `[⏹ Cancel download] [📥 Tasks]`.
- When real download percentage is unavailable, do not invent it.

At >30 seconds: `This download is taking longer than usual. I’m still working on track {current}/{total}.` At >60 seconds: `Still downloading {title}. There’s no reliable finish time available yet.` Keep Cancel as long as it works.

**Success, single track:** send the MP3, album art, title, artist, duration; delete the status. Add 👍 to the triggering message when supported. No extra “All done” message.

**Success, playlist:** send tracks as they complete; keep a single editable progress message. Final summary:

> ✅ Playlist finished: {success_count}/{total_count} tracks sent.{optional_failure_line}

Optional failure line: `\n{failed_count} track(s) couldn’t be downloaded.`

Buttons: `[🔁 Retry failed]` when retry payloads exist, `[📥 Tasks] [🏠 Home]`.

**Cancel:** stop current track where supported and do not start later tracks. Final message:

> ⏹️ Playlist cancelled. {success_count}/{total_count} tracks were sent; those files remain in the chat.

Buttons: `[📥 Tasks] [🏠 Home]`

**Per-track failure:** record which track failed and continue with the next item unless the user cancels or the whole service is unavailable. Never silently skip failures.

### 6.2 Instagram (F2)

**Accepted input:** post, reel, story URL; `@username` for profile picture only in Instagram input mode.

**Start status:** `📥 Got it — checking this Instagram link…`

**Status sequence:**
- 5s: `🔎 Getting Instagram media details…\n⏱ {elapsed}s elapsed.`
- 30s: `Instagram is taking longer than usual to respond. I’m still trying.\n⏱ {elapsed}s elapsed.`
- 45s proposed timeout: use the service-side failure copy below, after cancel/detach behavior is confirmed.
- Story-specific phase only when the actual worker enters it: `🔎 Getting Story details…`

Use `[⏹ Cancel] [📥 Tasks]` when the operation can be cancelled. On a carousel/album, show `📤 Sending item {current}/{total}…` where the API exposes item count. Send media items in the tightest grouping Telegram supports, followed by one concise caption. If individual media messages are unavoidable, include the item count where possible and retain one shared status message until the batch completes.

**Success:** send photo/video/media; remove status; add 👍 to triggering message where supported. Caption should contain the original title/caption when useful, but remove `🆔 @harzad_bot`.

**Invalid/missing/private content:**

> I couldn’t access that Instagram post. Check that the link is correct and the content is available, then try again.

Buttons: `[🔁 Retry] [🏠 Home]`

**Instagram session/cookie/server problem:**

> Instagram access is temporarily unavailable on our side. Your link may be fine — please try again later.

Buttons: `[🔁 Retry] [📥 Tasks] [🏠 Home]`

Do not react 👎 for a server-side credential issue. Use a neutral status/error response and keep reactions non-intrusive.

### 6.3 YouTube (F4)

**Entry:** a YouTube video or playlist link pasted anywhere or sent from the YouTube input screen. Always show the format picker from §3.2.

On selection, status sequence:
- 0s: `📥 Got it — checking this YouTube link…`
- 5s: `🔎 Getting video details…\n⏱ {elapsed}s elapsed.`
- 30s: `YouTube is taking longer than usual to load this video. I’m still trying.\n⏱ {elapsed}s elapsed.`
- After extraction: `⬇️ Downloading {title}…\n⏱ {elapsed}s elapsed.` If a playlist count is known, use `item {current}/{total}`.
- 60s and later: standard long-task copy from §4.2. Show actual download progress only when yt-dlp provides it.

Buttons: `[⏹ Cancel download] [📥 Tasks]` where cancellation is possible. For playlists, cancellation stops subsequent items and reports completed/failed counts.

**File ≤50 MB:** send the video/audio, title, cover and duration when available; delete the task status; add 👍 to the triggering message. No extra completion message for a single file.

**File >50 MB:**

> This file is too large to send in Telegram. You can download it from the link below.\nCheck the link’s expiry before sharing it.

Buttons: `[📋 Copy download link] [🏠 Home] [📥 Tasks]`

The actual expiry must be included only when the file host can report a real expiry. If there is no known expiry, use: `The link may stop working if the temporary file is removed.` Never invent an expiry duration.

**Unsupported/invalid YouTube URL:**

> That doesn’t look like a supported YouTube video or playlist link. Check the URL and send it again.

Buttons: `[🔁 Try again] [🏠 Home]`

**Stale picker:** toast `This link expired. Send it again.`

### 6.4 Pinterest (F5)

**Entry:** Pinterest URL pasted anywhere or sent after choosing Pinterest.

**Start/status:** same shared wait system, with `📥 Got it — checking this Pinterest link…`, `🔎 Getting Pinterest media details…`, and the shared 30s/60s long-task updates. Reuse the video-engine timeout guard until Pinterest-specific latency has been measured.

**Success:** send the media, remove status, add 👍 where supported.

**Invalid link:**

> I couldn’t recognize that Pinterest link. Send the link to a Pinterest video or Pin and try again.

Buttons: `[🔁 Try again] [🏠 Home]`

**Unavailable media/service:** use the service-side retry error in §7. Do not require users to know a specific `pin.it` URL prefix if valid Pinterest URLs from the platform are supported.

### 6.5 TikTok (F6)

Entry: TikTok link pasted anywhere. Start immediately and use shared status/cancel conventions. Do not state that watermark removal is supported; it is not available in the current implementation.

If the download succeeds, send the video and remove status. If the link is unsupported or the media is inaccessible, use the same actionable media-not-available pattern and retry button. TikTok should appear in Help/platform support copy, with a note that watermark removal is not available.

### 6.6 >50 MB file delivery

In Telegram chat, show a clear download link and copy button when a file exceeds the upload limit. Never create an unlabelled raw URL or imply it is permanent. The Mini App Download surface remains the preferred experience for large files because it already returns a download link. The Phase 3 self-hosted Bot API (2 GB uploads) is a roadmap decision, not a prerequisite for the P0 UX redesign.

---

## 7. Error design system (G3)

### 7.1 Error classes

1. **Input can be fixed by the user:** invalid URL, unsupported domain, query too short. Explain what input is expected and provide Retry/Home.
2. **Media unavailable:** deleted, private, geo/permission restricted, or not extractable. Say the content could not be accessed; suggest checking availability or trying another link.
3. **Temporary service/proxy failure:** make clear the problem is on Harzad/service side; offer Retry.
4. **Task timeout/cancellation:** say whether the task stopped, was cancelled, or may still be unwinding. Preserve completed files.
5. **Stale action:** short toast (not a modal alert) with a next step.
6. **Unexpected internal error:** fallback only; include Retry when the request is safely repeatable and a Home action. Record diagnostic details server-side, never expose them in chat.

### 7.2 Exact error copy and actions

| Trigger | Exact message | Buttons/toast |
|---|---|---|
| Invalid generic link in a link-only screen | `That doesn’t look like a supported media link. Send a link from YouTube, Instagram, Pinterest, TikTok, or Spotify.` | `[🔁 Try again] [🏠 Home]` |
| Unsupported domain | `I can’t download from {domain} yet. I support YouTube, Instagram, Pinterest, TikTok, and Spotify.` | `[🏠 Home]` |
| Media not found/private | `I couldn’t access this media. Check that it still exists and is available, then try again.` | `[🔁 Retry] [🏠 Home]` |
| Temporary platform/proxy failure | `{platform} is temporarily unavailable on our side. Please try again shortly.` | `[🔁 Retry] [📥 Tasks] [🏠 Home]` |
| Instagram session expired | `Instagram access is temporarily unavailable on our side. Your link may be fine — please try again later.` | `[🔁 Retry] [🏠 Home]` |
| Spotify search backend down | `Music search is temporarily unavailable on our side. Your query is fine — please try again shortly.` | `[🔁 Retry search] [🏠 Home]` |
| AI model timeout | `I couldn’t finish that answer in time. Please try again with a shorter question.` | `[🔁 Retry] [🏠 Home]` |
| File too large | Use §6.3 direct-link copy | `[📋 Copy download link] [🏠 Home]` |
| No preview found | N/A in chat body | Toast `No 30-second preview is available.` |
| Stale search result | N/A in chat body | Toast `This result expired. Search for the song again.` |
| Stale search cancel | N/A in chat body | Toast `This search has already finished.` |
| Stale YouTube picker | N/A in chat body | Toast `This link expired. Send it again.` |
| Stale task cancel | N/A in chat body | Toast `This task has already finished.` |
| Old AI text-to-image button | N/A in chat body | Toast `Image generation isn’t available yet. Try AI chat.` |
| Generic unexpected exception | `Something went wrong on our side. Please try again.` | `[🔁 Retry] [🏠 Home]` |
| DB/logging outage | No user-facing error if feature works | No action; retain silent circuit-breaker behavior |
| Process restart / token gone | `This action expired after a restart. Send the link or search again to start a new task.` | `[🔎 Search music] [🏠 Home]` |
| Second request while another task runs | Accept the task and list both in Tasks; do not interleave updates in one status message | Each task has its own status and Cancel token |

All toasts and alerts must be ≤64 characters. Use alerts only when the user must read/confirm a blocking issue; routine stale and success feedback uses toasts.

### 7.3 Retry rules

- Retry replays the original input/format only if that payload is still valid and safe to reuse.
- Retry creates a new task token and idempotency key. It must not duplicate a file send if the previous send succeeded but the response was lost.
- For a playlist, retry only failed items, not the entire playlist, when the exact failed items are known.
- If the original input is not retained after restart, show the restart error and ask for the link/query again.

---

## 8. Tasks view and interruption behavior

Tasks is a lightweight chat-native job list, not a full queue-management product. This is a P0 feature because task awareness and cancellation are top-priority owner complaints.

### 8.1 No active tasks

> No active tasks right now.\nSend a supported link to download media, or search for a song by name.

Buttons: `[🔎 Search music] [🏠 Home]`

### 8.2 Active tasks

Display up to 5 active tasks in one message, most recently updated first. Use short rows such as:

> **Downloading:** {short_title}\n{phase} · {elapsed}s elapsed · {item_progress_if_known}

Each task gets one `⏹ Cancel` callback on its own row. If >5 active tasks exist, show the first five plus `Show more` using `task:page:<page>` or paginate; never silently omit all remaining jobs. Show running and cancel-requested states separately.

### 8.3 Completed/failed task context

In P0, retain a small in-memory recent-task list for the current process. Show a maximum of 5 recent completions/failures after active tasks, and do not imply the history survives a restart. Persistent task history is P1.

Examples:
- `✅ Get Lucky — sent`
- `⚠️ Playlist — 8/10 sent, 2 failed`
- `⏹️ Video download — cancelled`

A failed task has Retry only while its safe retry payload remains available. A completed task has no Cancel action.

### 8.4 Concurrent requests

Do not force a user to wait for a current task before pasting another link. Each operation gets its own task status and token. Avoid global mutable “current URL” or “current results” values. If backend limits require serial work later, communicate that in Tasks with an explicit queued status; do not silently create a queue in the first release.

---

## 9. AI chat (F9)

### Entry
Home → `🤖 AI chat`. No extra Chat submenu.

### Prompt
Use §2.2 AI Chat prompt. Be explicit that messages are independent because there is no conversation memory in the current backend. Do not imply that follow-up questions share context.

### Request status
At 0s:

> 🤖 Thinking about your question…

At 5s:

> 🤖 Working on your answer…\n⏱ {elapsed}s elapsed.

At 15s:

> 🤖 This is taking a little longer than usual.\n⏱ {elapsed}s elapsed.

At 30s proposed timeout:

> I couldn’t finish that answer in time. Please try again with a shorter question.

Buttons while cancellable: `[⏹ Stop] [🏠 Home]` (`⏹ Stop` uses `task:cancel:<token>`); on timeout: `[🔁 Retry] [🏠 Home]`.

On success, send the answer as a new message, remove the status, and include `[Ask another question] [🏠 Home]` if there is room. Do not suggest the answer is a hosted GPT-class model; a short Help note may explain that this is a lightweight local assistant.

If a supported media link is pasted in AI Chat, the global dispatcher handles the link and the AI input mode remains available afterward. Do not send the URL to the LLM.

Conversation memory, streaming, and reliable stop are engineering features, not something copy alone can manufacture. Track them as P1/P2, depending on backend support.

---

## 10. Remove or defer dead features (F10)

Remove `🖼 Text to image` from all visible keyboards now. The handler/SDK is not operational, and a dead entry point damages trust more than a smaller AI menu.

Keep a defensive callback handler for old/stale `nav:ai:ttI` callbacks. It must answer immediately with toast `Image generation isn’t available yet. Try AI chat.` and never leave the client spinner active. Reintroduce the option only after an end-to-end engine test passes, with progress, cancellation, failure handling, and delivery behavior specified.

Remove the lyrics track-card button until a real provider is live. Do not render dead controls as disabled; omit them entirely.

---

## 11. Group-chat behavior and content sharing (G4)

### 11.1 Supported group use

- Inline music search (`@harzad_bot <song>`) is supported.
- A user may explicitly mention the bot with a supported link if Telegram group privacy settings allow it to receive the message.
- Do not promise the bot automatically reads all group messages.
- Search results and buttons in a group must not depend on the initiating user's private conversation state.

### 11.2 Shared card ownership

- Shared inline cards use a global/short-lived immutable token mapping to track metadata, not a per-user candidate index.
- Any group member may tap Preview or Download. The action runs for that clicking user; if the action sends media in the group, the button label must clearly indicate that the result will be sent **here**.
- Avoid showing one user's private query, active task list, account identifiers, or download state to other people.
- If a callback cannot be supported because the token expired, show a clear stale toast; never use another user's current search result as a fallback.

### 11.3 File protection policy

The current implementation uses `protect_content` on Spotify audio, which prevents forwarding. This is a product-policy choice, not just styling. **Recommended default:** allow forwarding for content delivered by the bot because sharing is a stated music-bot job. However, do not silently change production protection behavior without owner approval; confirm the decision in §15 before release. Whatever policy is chosen must be consistent between private downloads and inline/group results.

---

## 12. Mini App specification (G5)

The Mini App should share Harzad's names, status vocabulary, error tone, and accent color with the bot. The bot and Mini App remain different interfaces, but not different products.

### 12.1 Responsibilities

- **Telegram chat bot owns:** frictionless link dispatch, simple download delivery, one-tap song search, previews, AI one-shot chat, inline group discovery, task cancellation through chat.
- **Mini App owns:** large-file download links, visual music browsing/grid when implemented, in-app audio controls/queue when implemented, fuller task list/history when implemented.
- Do not force a user into the Mini App for a normal short download. Do not try to reproduce a visual grid/player in Telegram inline keyboards.

### 12.2 Tabs

**P0:** retain current `Download` and `Chat` tabs, align labels and error wording with the bot, add clear empty/loading/error/success states, and link back to the bot where possible.

**P1 proposed tabs:** `Download`, `Music`, `Tasks`, `Chat`.
- **Download:** URL field, explicit supported-platform hint, submit action, task status, cancellation, retry, final link with expiry caveat if known.
- **Music:** query input, cover grid/list, metadata, preview player, download action. Do not label results “instant” unless latency supports it.
- **Tasks:** active and recent task list with status, item counts, cancel/retry actions where supported.
- **Chat:** same local AI limitations as the bot; display that messages are independent until memory exists.

### 12.3 Mini App copy

Download placeholder: `Paste a media link to download it.`
Supported platform hint: `YouTube · Instagram · Pinterest · TikTok · Spotify`
Submit: `Download`
Loading: `Checking link…`
Empty tasks: `No tasks yet.`
Failure: `This download failed. Check the link or try again.`
Retry: `Try again`
Expired link: `This download link may have expired. Generate a new one to continue.`

### 12.4 Handoffs

- Bot → Mini App: use the chat menu `Open Harzad` for large downloads or visual browsing; preserve Telegram `initData` authentication and show degraded/anonymous behavior honestly.
- Mini App → bot: provide a clear `Open in chat` or share action when a track/media URL can be passed safely.
- Keep job IDs opaque. Do not expose internal UUIDs as user-facing identity.
- A job started in the Mini App must not be presented as a chat-delivered file if the existing product only returns a link; distinguish the delivery destination.

---

## 13. Help, onboarding, bot profile (F1 / F12)

### 13.1 Help message

> **How to use Harzad**\n\n• Paste a supported media link anytime to download it.\n• Tap **Search music** or type a song name from Search Music.\n• In any chat, type `@harzad_bot artist song` to find music inline.\n• Tap **Tasks** to check or cancel work that’s still running.\n• Use **AI chat** for a one-message question; each message is answered independently for now.\n\nSupported: YouTube, Instagram, Pinterest, TikTok, and Spotify.

Buttons: `[🔎 Search music] [📥 Tasks]` on row 1, `[🏠 Home]` on row 2.

### 13.2 First-run guidance

No separate tutorial is needed. The Home message must communicate both key actions: paste a link or search by song name. An optional example query can be a button only if it actually submits a predictable search; otherwise use plain example text. Do not include the numeric Telegram user ID.

### 13.3 Bot profile copy

**Description:** `Harzad downloads media from YouTube, Instagram, Pinterest, TikTok, and Spotify. Paste a link or search for a song by name. It also offers a lightweight AI chat.`

**Short description:** `Media downloads, music search, and AI chat 🎧`

**Commands:**
- `/start` — Open Home
- `/help` — How to use Harzad
- `/tasks` — View active tasks
- `/cancel` — Cancel a running task

---

## 14. Copy deck (G6)

Strings in this table are the canonical English source. Implementation should use stable localization keys instead of duplicating copy in handlers. Keep formatting/HTML escaping safe for user-controlled `{query}`, `{title}`, `{artist}`, `{album}`, `{domain}`, and `{first_name}` values.

| Key | Canonical English text / rule | Notes |
|---|---|---|
| `home.greeting` | `Hi, {first_name} 👋\nSend any supported media link here to download it, or search for a song by name.\nYou can paste a link anytime — no setup needed.` | No user ID |
| `home.search` | `🔎 Search music` | Primary CTA |
| `home.instagram` | `📷 Instagram` | Direct input prompt |
| `home.youtube` | `▶️ YouTube` | Direct input prompt |
| `home.pinterest` | `📌 Pinterest` | Direct input prompt |
| `home.ai` | `🤖 AI chat` | No image-generation button |
| `home.tasks` | `📥 Tasks` | Task view |
| `home.help` | `❓ Help` | Help page |
| `search.prompt` | `Type a song title, artist, or both.\nExample: Daft Punk Get Lucky\nYou can also paste a Spotify link anytime.` | One-tap search entry |
| `search.working` | `🔎 Searching for <b>{query}</b>\nMusic search usually takes about 45 seconds. You can keep using Telegram while I search.` | Initial status |
| `search.working_tick` | `🔎 Searching for <b>{query}</b>\n⏱ {elapsed}s elapsed. I’m still waiting for matches.` | 5s+ |
| `search.slow` | `🔎 Search is taking longer than usual for <b>{query}</b>.\n⏱ {elapsed}s elapsed. You can keep waiting or cancel.` | 30s |
| `search.very_slow` | `🔎 This search is slower than usual. I’ll show results if they arrive.\n⏱ {elapsed}s elapsed.` | 60s |
| `search.cancelled` | `⏹️ Search cancelled. Send another song name whenever you’re ready.` | Cancellation confirmed |
| `search.timeout` | `I couldn’t finish that search in time. Try again, or include both the artist and song title.` | Search timeout |
| `search.no_results` | `No matches found for <b>{query}</b>.\nTry the artist and title together, for example Daft Punk Get Lucky.` | No results |
| `search.failed` | `Music search is temporarily unavailable on our side. Your query is fine — please try again shortly.` | Backend/proxy down |
| `search.stale` | `This result expired. Search for the song again.` | Toast ≤64 chars |
| `search.cancel_stale` | `This search has already finished.` | Toast ≤64 chars |
| `search.cancel_button` | `⏹ Cancel search` | Cancel current search |
| `search.again_button` | `🔎 Search again` | Starts a new query prompt |
| `track.caption` | `🎵 <b>{title}</b>\n<i>{artist}</i>\n📀 {album} · ⏱ {duration}\nMatch {index} of {total}` | Omit missing metadata components |
| `track.download` | `⬇️ Download MP3` | Creates task |
| `track.preview` | `🎧 30s Preview` | Keep card visible |
| `track.previous` | `◀ Previous` | Edits same card |
| `track.next` | `Next ▶` | Edits same card |
| `track.no_preview` | `No 30-second preview is available.` | Toast ≤64 chars preferred; trim if required |
| `task.accepted` | `📥 Got it — checking your link…` | Immediate acknowledgment |
| `task.resolving` | `🔎 Getting media details…\n⏱ {elapsed}s elapsed. You can cancel anytime.` | 5s+ |
| `task.slow` | `🔎 This is taking longer than usual. I’m still trying.\n⏱ {elapsed}s elapsed.` | 30s |
| `task.downloading` | `⬇️ Downloading {short_title}…\n⏱ {elapsed}s elapsed.` | No fake ETA |
| `task.uploading` | `📤 Sending {short_title} to Telegram…` | Upload phase |
| `task.cancel_requested` | `⏹️ Cancellation requested. I’ll stop as soon as the current step can be interrupted.` | Only when not immediately interruptible |
| `task.cancelled` | `⏹️ Cancelled. {completed_count} file(s) already sent remain in the chat.` | Task cancelled |
| `task.playlist_done` | `✅ Playlist finished: {success_count}/{total_count} tracks sent.{optional_failure_line}` | Batch summary |
| `task.none` | `No active tasks right now.\nSend a supported link to download media, or search for a song by name.` | Task list empty |
| `task.stale` | `This task has already finished.` | Toast ≤64 chars |
| `yt.format_prompt` | `Choose a format for this YouTube link.` | Same across all entry points |
| `yt.format_video_720` | `🎬 Video · 720p` | Callback includes link token |
| `yt.format_video_360` | `🎬 Video · 360p` | Callback includes link token |
| `yt.format_mp3` | `🎵 Audio · MP3` | Callback includes link token |
| `yt.stale` | `This link expired. Send it again.` | Toast ≤64 chars |
| `instagram.prompt` | `Send an Instagram post, reel, or story link.\nFor a profile picture, send a username like @username.` | Input screen |
| `youtube.prompt` | `Send a YouTube video or playlist link.\nI’ll ask you to choose 720p, 360p, or MP3.` | Input screen |
| `pinterest.prompt` | `Send a Pinterest video link.` | Input screen |
| `ai.prompt` | `Ask me a question. I answer each message independently for now, so include any context I need in each message.` | No memory currently |
| `error.invalid_link` | `That doesn’t look like a supported media link. Send a link from YouTube, Instagram, Pinterest, TikTok, or Spotify.` | User fixable |
| `error.unsupported_domain` | `I can’t download from {domain} yet. I support YouTube, Instagram, Pinterest, TikTok, and Spotify.` | Include Home |
| `error.media_unavailable` | `I couldn’t access this media. Check that it still exists and is available, then try again.` | Retry/Home |
| `error.temporary_service` | `{platform} is temporarily unavailable on our side. Please try again shortly.` | Retry/Home |
| `error.instagram_session` | `Instagram access is temporarily unavailable on our side. Your link may be fine — please try again later.` | Server-side issue |
| `error.ai_timeout` | `I couldn’t finish that answer in time. Please try again with a shorter question.` | Retry/Home |
| `error.unexpected` | `Something went wrong on our side. Please try again.` | Final fallback only |
| `error.restart` | `This action expired after a restart. Send the link or search again to start a new task.` | No stale in-memory task |
| `error.too_large` | `This file is too large to send in Telegram. You can download it from the link below.\nCheck the link’s expiry before sharing it.` | Only state real expiry if known |
| `error.preview_missing` | `No 30-second preview is available.` | Toast; ≤64 chars |
| `error.image_generation_unavailable` | `Image generation isn’t available yet. Try AI chat.` | Stale legacy callback only |
| `help.body` | See §13.1 | Stable platform list |
| `mini.download.placeholder` | `Paste a media link to download it.` | Mini App |
| `mini.supported_platforms` | `YouTube · Instagram · Pinterest · TikTok · Spotify` | Mini App |
| `mini.download.submit` | `Download` | Mini App |
| `mini.download.loading` | `Checking link…` | Mini App |
| `mini.tasks.empty` | `No tasks yet.` | Mini App |
| `mini.download.failed` | `This download failed. Check the link or try again.` | Mini App |
| `mini.retry` | `Try again` | Mini App |
| `mini.link.expired` | `This download link may have expired. Generate a new one to continue.` | Mini App |

**Localization:** P0 ships English copy with all strings keyed and externalized. P1 adds Persian localization, initially selected from Telegram `language_code` where available, plus a manual language preference. Review Persian RTL keyboard labels and mixed LTR items (URLs, filenames, song titles) in a real Telegram client; do not assume translated copy alone completes RTL support.

---

## 15. Owner decisions and open questions (G8)

These decisions are product policy and should be answered before the related behavior is shipped. Defaults below are recommendations.

| Question | Recommended default | Priority |
|---|---|---|
| May downloaded audio/video be forwarded? Current Spotify audio uses `protect_content=True`. | Allow forwarding because sharing is a core use case, unless there is an explicit product/legal reason to prevent it. Keep current behavior until owner approval. | P0 decision |
| Should the product remain English-only? Audience skews Persian. | Ship P0 with localization-ready strings; prioritize Persian as P1 with manual override. | P1 |
| Should text-to-image be visible while the engine is missing? | No. Remove the button until a real end-to-end flow is available. | P0 |
| Are lyrics available now? | No. Remove the button until a working provider exists. | P0 |
| Can cancellation stop each worker process, including yt-dlp and Telegram upload? | Confirm per operation; do not show a working Cancel control when it cannot stop the current phase. | P0 |
| Can the search service yield partial results? | Investigate. Use partial results if the backend can emit them; otherwise keep truthful wait states and prioritize reducing the 45-second backend wait. | P0 investigation / P1 implementation |
| Can one result card be edited to swap cover media in the deployed PTB/Bot API path? | Test `editMessageMedia` with current message types; fallback to a single text card if it is unreliable. | P0 engineering check |
| What should happen when a shared inline button is tapped in a group? | It sends the selected result into that group, with button labels making the destination clear. | P0 |
| Should the 90s search and other proposed timeout guards be hard limits? | Validate against production telemetry before enforcing. Do not terminate real long downloads without measured justification. | P0 engineering check |
| Should the product move to a local Bot API server for 2 GB uploads? | No, not for this redesign; decide as a Phase 3 infrastructure project. | P2 |

---

## 16. Existing button/callback audit

Every current callback/button is accounted for below. New callback payloads must remain ≤64 bytes; use short opaque tokens and validate at runtime.

| Current callback/button | Disposition | New behavior / compatibility rule |
|---|---|---|
| `nav:start` / Home 🏘 | Keep as alias | Go to Home; do not cancel tasks. New keyboards may use `nav:home`. |
| `nav:ig` | Change | Opens Instagram input directly, removing one navigation hop. |
| `nav:ig:dl` | Retire from visible UI | Legacy button opens the same Instagram input prompt. |
| `nav:music` | Change | Opens Search Music prompt. |
| `nav:music:spotify` | Keep as alias | Opens Search Music prompt; both song text and Spotify links are accepted. |
| `nav:pin` | Keep/relabel | Opens Pinterest input prompt directly. |
| `nav:yt` | Change | Opens YouTube input prompt; any link still displays format picker. |
| `nav:yt:dl` | Retire from visible UI | Legacy button opens YouTube input; do not force 720p. |
| `nav:yt:mp3` | Retire from visible UI | Legacy button opens YouTube input; show the universal picker, do not silently force MP3. |
| `nav:ai` | Change | Routes to AI Chat directly. |
| `nav:ai:chat` | Keep as alias | Opens AI Chat prompt. |
| `nav:ai:ttI` | Remove visible entry | Legacy/stale callback gets `Image generation isn’t available yet. Try AI chat.` toast. |
| `yt:fmt:720` / `yt:fmt:360` / `yt:fmt:mp3` | Replace | Use `yt:fmt:<token>:720`, `:360`, or `:mp3`; old payload works only if its exact link token is still available, otherwise stale toast. |
| `song:dl:<i>` | Replace | Use `song:dl:<token>:<index>`; no reliance on mutable current per-user list. |
| `song:prev:<i>` | Replace | Use `song:prev:<token>:<index>`; stale toast if token invalid. |
| `song:lyr:<i>` | Remove visible button | Keep defensive stale callback response only if deployed old cards may still be tapped: `Lyrics aren’t available yet.` |
| Spotify URL button | Keep | Open Spotify URL; omit when absent/invalid. |
| `srx:c:<8-hex>` | Keep as legacy alias | Maps to the task cancellation token if it still exists; otherwise stale toast. New: `search:cancel:<token>`. |
| Back buttons | Standardize | Use `nav:back:<screen>` or map screen→parent server-side. Fix YouTube Back bug. |
| Home buttons | Standardize | Use `nav:home`; `nav:start` remains a compatibility alias. |
| `📋 Copy link` | Keep | Copy exact generated URL; show a small toast if supported. Include expiry caveat in the message, not in a fake clipboard success claim. |
| New `📥 Tasks` | Add | `nav:tasks`; opens task list without changing active jobs. |
| New `❓ Help` button | Add | `nav:help`; displays Help copy with Home. |
| Task-list pagination | Add when needed | `task:page:<page>`; stale page falls back to current Tasks view. |
| New Cancel download/AI Stop | Add | `task:cancel:<token>`; stale toast once task has ended. The AI `⏹ Stop` button uses the same task cancellation path. |
| New Retry | Add | `task:retry:<token>`; only when retry payload is safe/available. |
| New Search music | Add | `nav:search`; one tap from Home. |
| New Search again | Add | `search:again:<token>`; returns to query prompt or retries exact query only when label says Retry. |
| New carousel navigation | Add | `song:prevcard:<token>:<index>` and `song:next:<token>:<index>`. |
| New format cancel | Add | `yt:cancel:<token>`; discards that picker URL only. |

Callback handler invariants:
- Answer every callback query within 10 seconds; in practice acknowledge before network work.
- Use toast for routine acknowledgement/stale state and alert only for a blocking issue needing explicit reading.
- Never resolve a stale callback against “the most recent” query/link. Each token identifies the original immutable action.
- Use server-side authorization for private task actions (`owner_user_id` must match the clicker); shared inline cards are the deliberate exception and use public track metadata, not private task tokens.

---

## 17. Priorities and acceptance checklist (G7)

### P0 — required before calling the redesign complete

- [ ] Home copy removes numeric user ID and explains both paste-link and song-search jobs.
- [ ] Search Music is one tap from Home.
- [ ] YouTube link behavior is identical from every entry point and always shows the same format picker.
- [ ] Acknowledgement is sent before non-critical DB work; target is under one second.
- [ ] All operations expected to exceed 10s have a working Cancel control or an honest explanation of why the current phase cannot be cancelled.
- [ ] Spotify playlist progress reports item N/M; failures are counted and never silently skipped.
- [ ] Task registry/list shows active jobs and prevents global mutable URL/result collisions.
- [ ] Navigation never implicitly cancels a task; `/start`, Home, `/help`, `/tasks`, `/cancel` behavior is defined.
- [ ] Search has honest 0/5/30/60s messages, a timeout result, cancellation, and a retry path.
- [ ] Search results no longer require three separate photo messages; one editable card is implemented or a single text-card fallback is used.
- [ ] Generic, user-fixable, platform-side, timeout, stale, and internal errors are distinct and actionable.
- [ ] AI image generation and lyrics dead buttons are removed from visible UI.
- [ ] Instagram/server credential failures do not blame the user or trigger misleading 👎 feedback.
- [ ] `YOUTUBE_DOWNLOAD` Back-to-Instagram bug is fixed.
- [ ] Group inline track buttons work for other members or clearly fail with an appropriate stale/unsupported message; no personal state is exposed.
- [ ] All new callbacks are ≤64 bytes and stale actions have behavior defined.
- [ ] Large-file messages communicate link reliability honestly and never invent an expiry duration.
- [ ] Every user-facing message is keyed in one copy deck and all interpolation is escaped.

### P1 — after P0 stabilization

- [ ] Persian locale with language preference and RTL QA.
- [ ] Persistent job history/favorites across restarts if users demonstrate demand.
- [ ] Search more/refine and partial results if backend capability supports it.
- [ ] Mini App Music and Tasks tabs, with shared status/error language.
- [ ] AI memory/streaming/stop only when backend implementation can honor those controls.

### P2 — roadmap

- [ ] Lyrics provider, image generation engine, audio identification, video summarizer.
- [ ] Mini App visual discovery grid/audio player/queue.
- [ ] Self-hosted Bot API and 2 GB media delivery.

## 18. Measurement plan

Capture a baseline before release and compare after rollout. Do not judge the redesign by aesthetic preference alone.

| Metric | Definition | Success direction |
|---|---|---|
| Link acknowledgement latency | Link received → first visible bot reply | P50 <1s, P95 as low as feasible; DB logging must not gate reply |
| Download completion rate | Started eligible jobs that deliver at least one result | Increase |
| Search abandonment | Search tasks cancelled or abandoned before results | Decrease, while preserving visible Cancel |
| Search-to-download conversion | Search sessions resulting in an MP3 download | Increase |
| Search latency | Query submission → first result | Reduce backend time; report P50/P95 separately from UX wait copy |
| Task cancellation success | User cancel requests that actually stop the worker/remaining playlist | Increase; never count a UI acknowledgement alone as a successful cancellation |
| Silent failure rate | Tasks that end without file, error, or final status | Zero |
| Retry success | Retries that subsequently deliver a result | Increase |
| Stale callback rate | Taps on invalid tokens/restarted state | Decrease; remaining stale taps should get a clear toast |
| Group-card action success | Non-searcher group taps resulting in the intended action | Increase; no personal-state leakage |
| First-use completion | First-time users completing a download within two minutes | Increase toward the product target |

Instrument task phase, platform, result class, elapsed duration, cancellation requested/confirmed, and per-item failure counts. Avoid logging raw sensitive content beyond the current product's justified retention policy. Dashboard metrics should distinguish backend latency from time the UI spent without an update.

---

## 19. Implementation notes and scope boundaries

- Treat `PRODUCT.md`'s measured latency as the baseline. The wait copy is a design recommendation; proposed hard timeouts are hypotheses requiring telemetry validation.
- Make first-response latency an engineering acceptance criterion. The current synchronous DB user sync is visible to users; move non-critical logging off the critical response path.
- The task registry, cancellation tokens, immutable URL/result tokens, group-safe inline payloads, and retryable failed-item records are implementation requirements for the P0 behavior. They are compatible with the current in-memory architecture but will not survive a process restart; provide the restart fallback and plan persistence separately.
- Telegram cannot provide a real progress bar in chat. Use status text and real counters, and move visual browsing/player UI to the Mini App.
- Do not promise streaming AI responses, partial search results, exact download percentages, reliable ETAs, watermark removal, lyrics, or image generation until the backend exposes and passes those capabilities.
- Before release, test all buttons with a normal private chat, a group inline query from a second user, expired tokens, a process restart, one failed item in a playlist, cancel during download, cancel during upload, and a slow/unavailable proxy.
