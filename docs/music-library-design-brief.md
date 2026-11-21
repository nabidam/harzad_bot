# Music Library UX — Design Brief (Handoff to Expert Designer)

**Product:** Harzad — a personal Telegram assistant bot (`@harzad_bot`)
**Date:** 2026-11-21
**Author:** implementation team (Python / python-telegram-bot 22.8, Bot API 10)
**Status:** the feature is built and live; the UX is under review. Two designs were
already built and rejected by the owner. We need a complete, final plan for the
"My music" area.

**You (the designer) are asked for:** the full UX plan for the owner's music
library in the Telegram chat — every screen, state, label and callback — covering
today's library surface and the upcoming Playlists feature, plus a clear line of
what stays in chat and what waits for the Mini App (P2). Section 9 lists
everything we need answered; section 10 defines the deliverable format.

---

## 1. The product in one paragraph

Harzad is a single-user-in-practice Telegram bot. The owner pastes Spotify links
or searches for songs; the bot downloads and sends the MP3 in chat. The bot also
downloads Instagram/YouTube/Pinterest media and has an AI chat tab — but music is
the core habit. The **Music Library** ("My music") is the owner's personal
collection of everything he ever asked for: every delivery is recorded
automatically (title, artist, album, duration, cover URL, Spotify URL, when first
requested, how many times requested, and — when available — the Telegram cloud
handle that makes re-sending instant). The library's job: **re-listen to any
track you ever got, without re-downloading it**, and manage it (remove, later:
playlists).

## 2. The user

- The owner: a Persian speaker (Persian localization is planned, English is the
  canonical UI language for now), casual power user, phone-first.
- 25 days of real usage: **64 tracks**, ~2.5/day, rock/metal + Persian pop.
- **13 of the 64 track rows were requested 2–5 times** (2 tracks ×5, 3 ×3, 8 ×2) —
  re-listening is a real, frequent habit, not an edge case.
- He thinks in "my music" as a collection, not as a history log — but the data
  that exists *is* a request history (see §6).

## 3. Scope

**In scope (this brief):**
- The `📚 My music` area of the chat: entry point, navigation, all screens,
  all states, all copy, all button layouts.
- Playlists (Phase B — CRUD + "Send all" batch re-send), designed into the same
  navigation so it lands without a rework.
- The chat/Mini-App split: which capabilities chat keeps and which are deferred
  to the Mini App Music tab (P2, a web SPA with a grid, search, player,
  playlist builder — the heavy browsing surface, when it ships).

**Out of scope:** the search/download flows (they have their own spec and are
frozen), the Mini App UI itself (P2), tags, lyrics, stats, sharing.

## 4. Hard platform constraints (Telegram Bot API — do not design around these)

1. **One screen = one bot message.** Text ≤ 4096 chars, HTML subset only
   (`<b> <i> <u> <s> <a> <code> <pre> <blockquote>`). No custom fonts, no
   color, no layout beyond line breaks. Telegram renders it in its own
   theme (dark or light — uncontrollable).
2. **Buttons are an inline keyboard** under the message: rows of buttons,
   top→bottom. Up to 100 buttons per message, but anything over ~8 rows or
   ~10 buttons per row renders badly. A button label is effectively one
   line (~25–30 chars before it wraps and the row misaligns). Buttons can be
   *callback* (payload ≤ 64 bytes) or *URL* (opens app/website) or *copy-text*.
   One style bit per button (primary/neutral). No icons other than emoji.
3. **Navigation is in-place card editing.** There is no app frame: the bot
   edits the current message into the next "screen" when a button is tapped.
   The user scrolls chat history like a log; **the UI model is one active
   card that morphs**, plus freshly sent media messages. Design for that
   model: every screen must carry its own way back (no system back button).
4. **No text input via buttons.** Asking the user for text (e.g. "type the
   song name") requires a conversation turn (the bot says "send me the name",
   the next user message is the input). The bot already runs such states for
   music search — in-library search can do the same.
5. **The bot cannot read chat history.** Only data we store exists. (This is
   why the library rows are what they are — §6.)
6. **Private-chat only.** Owner decision: nothing library-related is ever
   rendered into a group chat; a tap there gets one toast. Design 1:1.
7. **Media actions live on media messages.** When a track is re-sent, the
   audio message carries action buttons; those can fire actions but should
   not be the place where navigation happens (a tap there renders a new text
   card next to it).
8. **Rate limits (429).** Any single send can be rate-limited with a
   `retry_after` in seconds. Every flow that sends must have a "wait N s and
   tap again" state. Bulk sends (playlist) are paced ~1 msg/s.
9. **Stale state.** Card buttons reference a frozen snapshot (300 s TTL,
   in-memory; a process restart clears it). Expired/unknown card → one toast,
   user re-opens. Design copy for that.
10. **Localization (P1):** every user-facing string must come from a flat
    copy deck (stable keys, no formatting inside). Expect Persian later:
    strings can grow ~30%, mixed RTL content (real titles like
    `OUD SILENCE " صَمْت "` exist — see §6). Button labels should stay short
    and emoji-anchored so they survive translation.

## 5. Current state (baseline you are iterating on)

Two versions were built and rejected. Do not redesign from scratch blindly —
know what exists, what the owner rejected, and why (our reading of the "why"
is labeled as hypothesis).

### v1 — single-track card (rejected)
`My music` opened **one track per card**: cover photo, title/artist/album,
"Track i of n", buttons Get again / 30s Preview / Remove, carousel
Previous/Next within the page of 10, Prev page/Next page between pages.

Owner verbatim:
> "we need much better UX for this part. this has more steps to find the
> track. no need to show the cover image and tracks one by one. I was
> thinking of a second menu in 'my tracks', for example one is 'latest', then
> send 5 latest tracks full, and one 'more' key. and other second step
> buttons."

### v2 — menu + list of 5 (current, rejected as "not good enough yet")
- **Menu screen** (what `📚 My music` renders, in-place edit):
  ```
  📚 Your music
  64 tracks · newest first

  [🕒 Latest] [📄 More]        [📥 Tasks] [🏠 Home]
  ```
  (`More` hidden when only one page exists.)
- **List page** (Latest/More render it, in-place edit) — 5 tracks in full,
  no covers:
  ```
  📚 Your music · page 1 of 13

  1. Encounters at the End of the World
     Thomas Feiner · Encounters at the End of the World · 3:40
     📥 3× · last 29 min ago

  2. Candles
     Resa Saffa Park · Spaces (Deluxe) · 3:32
     📥 1× · last 8 h ago
  …
  [⬇️ Get again] [🎧 30s Preview] [🗑 Remove]   ← one row per track (5 rows)
  [◀ Newer] [Older ▶]
  [📚 Menu]
  [📥 Tasks] [🏠 Home]
  ```
- **Empty:** "No music saved yet. …" + `[🔎 Search music] [🏠 Home]`
- **DB down:** "…temporarily unavailable… try again shortly." +
  `[🔁 Try again] [🏠 Home]`
- **Toasts:** list expired / removed / generic failure / private-chat warning.
- **Get again** semantics (fixed, not designable): instant cloud relay when the
  row has a stored cloud handle (most rows after their first re-send);
  otherwise a 1–2 min re-download that runs as the normal task status (cancel
  included). On success the row is refreshed, so the *first* re-send of an old
  track is slow and every later one is instant.
- **30s Preview:** 1–3 s lookup, then Telegram streams the MP3; may fail →
  toast. **Remove:** deletes the row, toast, list re-renders.

Tap counts today (Home → target):
| Task | Taps |
|---|---|
| Re-listen to one of the 5 newest | 3 (My music → Latest → Get again) |
| Re-listen to tracks 6–10 | 3 (My music → More → Get again) |
| Re-listen to track ~40 | 6 (… → More → Older → Older → Older → Get again) |
| **Find a specific old track by name** | **unbounded — there is no search, no sort, no index; worst case paging through 13 pages** |
| Remove a track | 3–4 |

Our honest hypothesis of the remaining gap (the owner did not say more than
"not good enough yet" — validate or correct it in your plan):
1. **Finding a specific track is unsolved.** Pagination is browsing, not
   finding. With 64 tracks it already fails; at 500 it is unusable.
2. The menu still adds a step between "My music" and "I want my songs".
3. Three buttons per row × 5 rows is dense; the primary action (Get again)
   competes visually with Remove.
4. "Newer/Older" chain has no orientation (where am I, how far is the end).

## 6. The data (what a row is; design against this)

Per track we store exactly:

| Field | Notes |
|---|---|
| `title` | 4–109 chars. Real worst case: `Flower In A Hurricane (feat. Tame Impala, SZA, King Gizzard & the Lizard Wizard) [From GTAVI: Original Score]` (109). Mixed Latin/Persian/diacritics occur. |
| `artist` | e.g. `Justice`, `Sertab Erener`, `Kamuran Akkor` |
| `album` | e.g. `Spaces (Deluxe)` — **64 tracks sit on 63 distinct albums**; album is weak as a grouping key. |
| `duration_sec` | present on all resolved rows; 2:27–8:53 |
| `cover_url` | Spotify CDN, always present, can go stale; **owner said: no covers in chat** (treat as fixed for now — you may challenge it once, explicitly). |
| `spotify_url` | deep link; usable as a URL button (opens Spotify app) |
| `request_count` | how many times the owner asked for it (1–5 in current data) |
| `added_on` / `last_requested` | real history timestamps |
| cloud handle (file_id + message refs) | present on rows re-sent after capture; decides instant vs slow re-send |

Real samples (from the live library, 2026-11-21):
```
 3×  Encounters at the End of the World — Thomas Feiner — 3:40
 2×  Because You Love Me — Kaz Hawkins — Feelin' good — 5:43
 2×  Yesterday — breathe. — 3:24
 1×  Lost in a Dream — Guenter Haas — Tales of a Broken Planet — 6:42
 1×  Aşkım Ve Gururum — Kamuran Akkor
 1×  Kendine Aşık — Sertab Erener
 1×  OUD SILENCE " صَمْت " — ALTurkTwins
 1×  Flower In A Hurricane (feat. Tame Impala, SZA, King Gizzard …) — Justice — 109-char title
```
Growth assumption for design: **plan for 500–1,000 tracks** (2–3/day habit).
A "favorite" concept is plausible (request_count ≥ 3 ≈ 5 tracks today) but no
explicit favorite data exists — you may design one (e.g. "often played" derived
from request_count, or a star action if you think it earns its button).

## 7. Behaviors you can rely on (implementation, fixed)

- Every delivery auto-records a row (no user action); deduped per user +
  Spotify track id. The library is *always* populated by use.
- DB reads are millisecond-fast; the UI is not latency-bound except:
  re-download (1–2 min, has its own status/cancel), preview lookup (1–3 s),
  and 429s (seconds).
- One in-memory token per rendered list (300 s TTL) freezes what the buttons
  point at; any number of screens/buttons is implementable within that, as
  long as each button payload ≤ 64 bytes.
- Existing global keys: `[🏠 Home]` and `[📥 Tasks]` are the standard escape row
  of every screen in the bot (spec convention) — keep them last on every
  library screen unless you have a strong reason not to.

## 8. Design goals (the owner's, in his words + ours)

1. **"Fewer steps to find the track."** (verbatim, about v1 — still the governing complaint.)
2. **Batch, not one-by-one.** Tracks shown together, "full" (all useful
   metadata), no covers. (verbatim)
3. **A menu with second-step buttons.** The owner explicitly wants My music to
   open a second-level menu (Latest / More / …), not dump a list. (verbatim)
4. **Finding, not just browsing.** (ours — the unbounded "find a specific old
   track" is the biggest hole in v2.)
5. **Room for Playlists** (create/rename/delete, add/remove tracks, "Send all")
   without another rework.
6. **Honest states.** Every failure (DB down, expired card, 429, no preview,
   un-re-fetchable track) gets a defined, non-alarming screen or toast.

## 9. Questions we need answered (numbered — answer all, or say "defer" with a reason)

1. **Navigation model.** How should the owner get from "My music" to *any*
   track in the library, at 64 and at 500 tracks? Options we can implement
   cheaply: in-library search (type name → filtered list, ms fast), sort
   switch (recent / added / A–Z), "often played" section, A–Z jump, playlists
   as the organizing structure. Give 2–3 concrete models with tap-count
   tables for both library sizes, and recommend one.
2. **First screen.** Menu with no tracks (v2) vs. newest tracks immediately
   visible with a compact options row vs. something else. What are the menu's
   second-step buttons exactly, at v1 of the feature and after Playlists land?
3. **List density.** Rows per screen (v2 used 5×3 buttons), what each row shows
   (title/artist/album/duration/count/last-played — trim to what earns space),
   and the button set per track. If more than 2–3 actions per track are needed:
   do they go on a per-track detail screen, or a "⋯" row?
4. **Per-track detail screen** — worth its tap? (cover, full metadata, open on
   Spotify, add-to-playlist, remove.) Yes/no/when.
5. **Sort and metadata display.** What's the default order? Is
   `📥 3× · last 8 h ago` worth keeping per row? Any of it move to a header
   instead?
6. **Pagination/positioning.** Replace "Newer/Older" with what? (numbered
   pages, jump-to, "you're at the end" marker, search-first so pagination
   barely matters, …)
7. **Search.** Spec it end-to-end if part of your model: trigger button,
   prompt copy, minimum characters, result layout (reuses the list?),
   no-result state, how the results snapshot behaves (the 300 s stale rule
   applies).
8. **Playlists surface.** Where do they live in the navigation (menu row?
   Home row?), what does the playlist screen look like (list of tracks with
   positions, add-to-playlist flow from a track row, rename/delete, "Send all"
   status and cancel), caps: 50 playlists / 200 tracks each (enforced with a
   clear toast).
9. **States matrix.** For every state — empty library, 1 track, 5 tracks,
   64, 500; expired card; DB down; 429 on send; preview unavailable;
   re-send ladder exhausted (track un-re-fetchable, honest class-2 message);
   group-chat tap — specify the exact screen or toast.
10. **Covers.** Owner currently says no covers in chat. Confirm, or make your
    one explicit case for where a cover would earn its space.
11. **Persian/RTL.** Any layout decisions that must change when copy switches
    to Persian (labels, alignment, mixed-RTL titles)?
12. **Chat vs Mini App.** Which of your design's elements do you push to the
    Mini App (P2) anyway, and at what library size does chat stop being
    adequate? (The Mini App gets: cover grid, full search, player with queue,
    playlist builder.)

## 10. Deliverable (what we implement)

A single markdown document (style: like this one — tables, exact strings, no
vague "etc."), containing:

1. **Navigation model** — one diagram (ASCII fine) of the whole My music area,
   plus the tap-count table for the five core tasks (newest track / any old
   track / remove / create+fill a playlist / re-send a playlist) at 64 and
   500 tracks.
2. **Full screen inventory.** For every screen: purpose, how it's reached,
   **exact text** (English copy, final — we paste it straight into the copy
   deck), **exact button grid** (rows × labels, primary/neutral noted), what
   each button does and its next screen, and how the user gets back.
3. **State matrix** (Q8/9) — same precision as screens.
4. **Copy deck** — every string as a stable key → value table, toasts included
   (toasts are hard-capped at 64 characters by Telegram — keep them under).
5. **Edge cases** — 109-char title, RTL title, 1 track, 0 search results,
   500 tracks, expired card, 429.
6. **Explicit deferred-to-Mini-App list.**
7. **Build order** — what ships first (we rework the existing v2 in place;
   name the minimal first increment).

**Acceptance bar** (we'll judge the plan against this):
- Any track in a 500-track library reachable in **≤ 5 taps** from Home.
- One of the 5 newest reachable in **≤ 3 taps** from Home.
- No screen with > 8 button rows or labels > ~30 chars.
- Every state in the matrix specified with exact copy.
- Nothing requires a platform capability outside §4.

## 11. Pointers (context, not required reading)

- `docs/music-library-plan.md` — architecture contract: data model, the
  re-delivery ladder (instant cloud relay → copy original message → re-download
  → honest failure), breaker-guarded writes, privacy model.
- The bot's existing UX spec (`HARZAD_UX_REDESIGN_SPEC.md`) for the global
  conventions: one primary CTA per screen, `[🏠 Home] [📥 Tasks]` escape row,
  toast usage, error classes.
- Everything in this brief is current as of 2026-11-21 (Bot API 10, PTB 22.8,
  64 live rows).
