# Harzad Bot — Music Library Plan

**Date:** 2026-11-21
**Status:** Agreed direction (owner). Phase A (schema/capture/v1 card) done 2026-11-21;
v2 menu + list rework done same day (owner rejected it as "not good enough yet").
**UX v3** — the expert design `docs/music-library-ux-design.md`, approved by the
owner 2026-11-21 — is now the contract; implementation plan in §12. Branch
`music-library`; live gate on the v3 rework, then merge `--no-ff` per repo workflow.
**Scope:** per-user music library, re-delivery of previously requested tracks, playlists
**Related:** `HARZAD_UX_REDESIGN_SPEC.md` (P1 "persistent job history/favorites", P2 "Mini App media hub"), `PRODUCT.md` §8 (persistence = medium cost), `docs/music-library-design-brief.md` (handoff brief), `docs/music-library-ux-design.md` (approved UX spec)

---

## 1. Problem

Today a requested song is gone after delivery: the MP3 is staging-only
(`cleanup_media` after `send_audio`), nothing music-related is persisted (DB has
only `users` and `logs`), the task registry and search cache are in-memory and die
on restart. "Get that song again" = 45 s search + 10–60 s download, every time.

Goal: each user can **review** their requested music, re-listen, organize into
playlists, and manage it — without re-downloading when we can avoid it.

## 2. Design decisions (agreed 2026-11-21)

| Decision | Choice | Rationale |
|---|---|---|
| What is persisted | **Track metadata only** (no MP3 on disk) | Root fs 95% full, /tmp 8 GB tmpfs, whole architecture is staging-delete. Re-listen = re-delivery from **Telegram's** cloud (below), not from our disk. |
| Re-delivery mechanism | Store `file_id` of the delivered audio; re-send via `send_audio(file_id)` | Officially documented, no re-upload, no size limit (§3). |
| Surface split | **Chat = thin manager** (list, add, remove, re-send); **Mini App = rich UI** (cover grid, player, queue) when built | Bot API has no grid/player widgets; spec §12.1/§19 already assigns visual browsing/player to the Mini App. |
| Playlists | Named user collections in DB, re-sent as a cancellable batch | Reuses task registry + status ticker. |
| Manual tags | **Deferred** | Artist/album come free from Spotify; free-text tags in chat add state complexity with no demonstrated demand. Auto-attributes (artist, album, source) are enough for v1. |

## 3. Re-delivery from Telegram's cloud (research, 2026-11-21)

Source: official Bot API reference (core.telegram.org/bots/api, current), verified
locally against PTB 22.8.

**Documented behavior ("Sending files"):**

> "If the file is already stored somewhere on the Telegram servers, you don't
> need to reupload it: each file object has a file_id field, simply pass this
> file_id as a parameter instead of uploading. **There are no limits for files
> sent this way.**"

- Re-sending by `file_id` keeps the file type (audio → audio); file_id is unique
  per bot; the same file can have multiple valid file_ids.
- `sendAudio` accepts `file_id` for its `audio` parameter ("Pass a file_id to
  send a file that exists on the Telegram servers (**recommended**)").
- `copyMessage` copies a message **without** a "Forwarded from" link and accepts
  a new `caption` (≤1024), `parse_mode`, `protect_content` and `reply_markup`.
- `forwardMessage` works too but carries the "Forwarded from" header — not used.
- Flood control: exceeding it returns 429 with `retry_after`; the numeric
  per-chat table was removed from the current docs page. Practical rule: pace
  batch re-sends ≈1 message/s per chat and honor `retry_after`.
- PTB 22.8 surface (verified): `Bot.send_audio(chat_id, audio: FileInput | ...)`,
  `Bot.copy_message(chat_id, from_chat_id, message_id, caption=, reply_markup=,
  protect_content=, ...)`, `Bot.forward_message(...)`.

**Delivery ladder for "get again" (per library row):**

1. **`send_audio(audio=file_id, title=, performer=, duration=)`** — primary.
   Server-side relay, no upload from our server, no disk. New clean message:
   own caption + library keyboard (no forward header). The re-sent audio keeps
   its baked-in title/artist/cover; do **not** pass `thumbnail` ("not possible
   to resend thumbnails").
2. **`copy_message(orig_chat_id, orig_message_id, reply_markup=...)`** — only if
   step 1 fails *and* the original message is known to still exist.
3. **Full re-download via `spotify_url`** (existing task flow) — last resort; on
   success the row's `file_id`/message refs are refreshed.

**Why the ladder is safe:** nothing about file_id lifetime is guaranteed by the
docs (a user may delete the original chat; Telegram may purge). The row stores
`file_id` **plus** `orig_chat_id`/`orig_message_id`, so each layer's failure has
a next step. A row whose ladder exhausts gets an honest failure card (error
class 3/6, spec §7) — never a silent skip.

**Batch (playlist re-send):** one task-registry entry (kind `playlist`), loop
rows in position order, status message with `track N/M` + elapsed, Cancel works
between items, 429 → honor `retry_after`, per-item failures counted (never
silently skipped — spec §6.1 rule). 100 tracks ≈ ≥100 s at per-chat pacing —
bounded by flood control only, no download or upload cost.

**Known gap (documented, accepted):** a single-track delivery whose
`send_audio` response timed out **after** full upload (in-flight case, incident
2026-11-17) has no usable response → no `file_id` captured. That row re-sends
via re-download. Rare; honest.

**Group safety:** file_id re-send works into any chat the bot is in, independent
of where the track was first delivered. Library rows are per-`tg_id`; every
library callback carries `owner_user_id` check (same rule as tasks, spec §16).
`protect_content=False` on all re-sends (owner decision, spec §11.3).

## 4. Data model

Postgres (`baaham-postgres`), schema managed by hand as today (legacy MySQL-dialect
Alembic migrations are never run — KB §3.5). Models follow `models/user.py` style.

```sql
CREATE TABLE tracks (
    id             BIGSERIAL PRIMARY KEY,
    tg_id          INTEGER NOT NULL,
    track_key      TEXT NOT NULL,              -- Spotify track id; sha1(title|artist|album) when absent
    title          TEXT NOT NULL,
    artist         TEXT NOT NULL DEFAULT '',
    album          TEXT NOT NULL DEFAULT '',
    duration_sec   INTEGER,
    cover_url      TEXT,
    spotify_url    TEXT,
    source         TEXT NOT NULL DEFAULT 'spotify',  -- v1: always 'spotify' (both entry paths); future: yt_mp3, ...
    file_id        TEXT,                       -- Telegram file_id of last delivered audio (cloud re-send)
    orig_chat_id   BIGINT,                     -- chat of the delivered message (copy_message fallback)
    orig_message_id BIGINT,                    -- message id of the delivered audio
    request_count  INTEGER NOT NULL DEFAULT 1,
    added_on       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_requested TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tg_id, track_key)
);
CREATE INDEX idx_tracks_tg ON tracks (tg_id, last_requested DESC);

CREATE TABLE playlists (
    id         BIGSERIAL PRIMARY KEY,
    tg_id      INTEGER NOT NULL,
    name       TEXT NOT NULL,
    created_on TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tg_id, name)
);

CREATE TABLE playlist_items (
    playlist_id BIGINT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id    BIGINT NOT NULL REFERENCES tracks(id)    ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    added_on    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (playlist_id, track_id)
);
```

Applied to `baaham-postgres` on 2026-11-21 (`CREATE TABLE IF NOT EXISTS`),
alongside the existing `alembic_version`, `logs`, `users`.

Implementation: `models/track.py`, `models/playlist.py`,
`models/playlist_item.py`; `cruds/library.py` (upsert/recent/count/delete/
touch + full playlist set); `utils/library.py` (facade, `LibraryTrack` dataclass).

Notes:

- **Dedup key:** prefer the Spotify track `id`. `connectors/spotify_search.py`
  `TrackResult` carried no id — `track_id` added (one field) so the search
  path gets a stable key too. URL path: spotdl `Song.song_id` (attribute,
  verified against the installed spotdl), raw `json["id"]` fallback, then
  `sha1(title|artist|album)`.
- **Capture at delivery (implemented):** `_deliver_spotify()` in
  `bot/handlers/music/spotify/download.py` now returns `(in_flight,
  file_ref)`; the run loop calls `utils.library.record_song_delivery(...)`,
  a breaker-aware fire-and-forget background task (same contract as
  `sync_user`/`log_message`): upsert the row, increment `request_count`,
  refresh `last_requested`, apply `file_ref = (file_id, chat_id,
  message_id)` only when the send response carried one. In-flight timeout
  → metadata-only row, previously stored handles never cleared.
- **DB soft-failure:** delivery capture and request counts are background
  tasks on the shared DB circuit breaker (KB §3.2, spec P0 rule: a DB outage
  must not break delivery). Reads run on a worker thread
  (`asyncio.to_thread`) and degrade to `None` → the honest "temporarily
  unavailable" card. Delivery never depends on any DB call succeeding.
- **Privacy (implemented):** per-`tg_id`; the whole view is private-chat
  only — a `📚 My music` tap in a group answers a toast and renders
  nothing. Every card callback re-checks the owner `tg_id` server-side.
- **History backfill (implemented):** `scripts/backfill_music_library.py`
  imports a user's past track URLs from the `logs` table (every pasted
  message is logged). Metadata-only — the Bot API has no chat-history
  endpoint, so old delivered messages' file_ids are unreachable and a
  backfilled row's "Get again" re-downloads until a fresh delivery
  captures cloud handles. `request_count`/`added_on`/`last_requested`
  come from the log rows; `cruds.library.backfill_track` is
  insert-if-absent, so live-captured rows are never touched. Idempotent;
  `--dry-run` / `--limit` supported.

## 5. Chat UX (Phase A — v1 + v2, both superseded)

> **Superseded by UX v3 (§12).** This section records the two chat designs
> that were built before the expert design landed (v1 one-track card,
> v2 menu + list of 5). The v3 screens replace both; the fixed plumbing
> here — `nav:library` entry, private-chat gate, tokenized 300 s
> snapshots, the §3 re-delivery ladder, breaker-guarded writes — all
> carry over unchanged.

Entry: new `📚 My music` button on the Home keyboard. **Deliberate deviation
from spec §2.1's 4-row layout (agreed):** Home is now 5 rows — row 4 =
`[📥 Tasks] [📚 My music]`, row 5 = `[❓ Help]` alone. Callback
`nav:library` (stateless, works in every state, same family as `nav:tasks`;
a data-driven card, not a NAVIGATOR state). Private chats only (§4 privacy).

**Empty state:**

> No music saved yet.\nSearch for a song or paste a Spotify link — everything you get lands here automatically.

Buttons: `[🔎 Search music] [🏠 Home]`

**Unavailable state** (DB down / breaker open / failed read):

> Your music library is temporarily unavailable on our side. Please try again shortly.

Buttons: `[🔁 Try again] [🏠 Home]` (Try again re-opens the view).

**Menu** (what `nav:library` renders — rework of 2026-11-21, owner
decision: the one-track card was too many steps to find a track; no cover
images, no one-at-a-time carousel):

> 📚 <b>Your music</b>
> {count} track(s) · newest first

Buttons: `[🕒 Latest] [📄 More]` (More only while a 2nd page exists) and
the escape row `[📥 Tasks] [🏠 Home]`. Phase B adds its rows (📃
Playlists) above the escape row — the menu is the extension point, no
dead buttons before a handler exists (spec §10).

**List page:** one editable text card of **5 tracks in full** (no covers),
newest request first, reusing the search-result tokenization (spec §5.3 —
frozen page snapshot, 300 s TTL store, owner-checked, stale toast).

Text (HTML, escaped), numbered rows:

> 📚 <b>Your music</b> · page {page} of {pages}
>
> 1. <b>{title}</b>
> <i>{artist}</i> · {album} · {m:ss}
> 📥 {request_count}× · last {relative time, "2 d ago"}
>
> 2. …

Keyboard:

| Row | Buttons | Callback |
|---|---|---|
| 1–5 | per track: `⬇️ Get again` (primary) · `🎧 30s Preview` · `🗑 Remove` | `lib:dl:<token>:<i>` · `lib:prev:<token>:<i>` · `lib:rm:<token>:<i>` |
| 6 | `◀ Newer` (page > 1) · `Older ▶` (page < pages) | `lib:page:<n>` |
| 7 | `📚 Menu` | `nav:library` |
| 8 | `📥 Tasks` · `🏠 Home` | `nav:tasks` · `nav:home` |

`<i>` is the 1-based row inside the token's frozen page snapshot — the
search card's index-in-payload pattern, so two rendered cards from the
same snapshot never point at each other. All ≤64 bytes (longest:
`lib:prev:` + 8-hex token + `:` + 2 digits = 19).

- **Get again** → the §3 delivery ladder. Cloud path: no download status —
  the file just arrives (early ack, `send_chat_action(UPLOAD_VOICE)` during
  the relay — PTB's constant for audio uploads, same as the download flow);
  re-download fallback runs the normal Spotify task status (whose delivery
  capture refreshes the row's cloud handles). On any successful re-send the
  row's `request_count`/`last_requested` bump in the background.
- **30s Preview** → iTunes/Deezer lookup (`connectors/preview.py`, the
  search-card path); Telegram fetches the MP3 itself — no staging. No
  preview found → toast (reuses the search toast).
- **Remove** → delete row (playlist items cascade), toast `Removed from
  your library.`, then the page is re-read from the DB and re-rendered
  (a now-dead page falls back to page 1). DB failure → honest failure
  toast, never a fake success.
- **Stale/unknown token, foreign owner, out-of-range index** → toast
  `This list expired. Open My music again.` and nothing runs.
- **429 on a re-send** → visible wait message carrying the `retry_after`
  seconds (the tap is still there); not a toast, since the early ack already
  went out.
- **Ladder exhausted** → message `I can't re-fetch this track anymore — …
  Search for it again to get it back.` + `[📥 Tasks] [🏠 Home]` (error
  class 2, spec §7) — never a silent skip.

## 6. Playlists (Phase B)

- `📃 Playlists` row on the My music menu (the §5 extension point).
- List: up to 10 playlists, `⏵ Send all` · `✏️` (rename) · `🗑` per row.
- **Send all** → task kind `playlist`: resolve each item's row, run the §3
  ladder per item, status `📤 Sending {playlist} — track {N}/{M}`, Cancel
  between items, final summary `✅ {playlist}: {ok}/{M} sent` (+ failed line).
  Items whose ladder exhausts are counted failed, never skipped silently.
- Remove-from-playlist, reorder (move up/down — position updates), rename.
- Caps (chat ergonomics): 50 playlists/user, 200 tracks/playlist. Enforced with
  a clear toast, not a silent cap.

## 7. Mini App (Phase C, P2)

New `Music` tab in the existing SPA (spec §12.2 P1 tabs): cover grid (covers
served via a read-only API route reusing `initData` auth — remote `cover_url`
proxied/cached as ≤480 px JPEG, same pipeline as `prepare_cover`), in-library
search, per-track re-send-to-chat action (calls the same delivery core),
playlist builder, in-app audio player with queue (the aiohttp Range-streaming
pattern from the Download tab already exists — `web/app.py` `/api/jobs/{id}/file`).
The in-app player needs the audio bytes: for cloud-stored tracks that means a
`getFile` round-trip on first use per track (20 MB cap for bot downloads) —
accepted for P2; chat re-send (Phase A) remains the primary "listen again"
path and has no such cap.

## 8. New callback inventory (≤64 bytes)

> **Superseded by UX v3 (§12.3)** — the callback families below (v1/v2) are
> retired; v2 payloads that may sit in the live chat become defensive
> stale-toast patterns.

Phase A (implemented):

| Callback | Button | Stale behavior |
|---|---|---|
| `nav:library` | 📚 My music | stateless — never stale (group: private toast) |
| `lib:dl:<token>:<i>` | ⬇️ Get again | toast `This list expired. Open My music again.` |
| `lib:prev:<token>:<i>` | 🎧 30s Preview | same toast |
| `lib:rm:<token>:<i>` | 🗑 Remove | same toast |
| `lib:page:<n>` | 🕒 Latest (1) · 📄 More (2) · ◀ Newer · Older ▶ | `n < 1` → page 1; `n > max` → toast `That page doesn't exist anymore.` |

Phase B: `lib:add:<token>:<i>` (➕ Add to playlist, same stale toast), plus
the `pl:*` family:

| Callback | Button | Stale behavior |
|---|---|---|
| `pl:list` / `pl:send:<pid>` / `pl:rm:<pid>` / `pl:rename:<pid>` | playlist mgmt | owner check + existence check → toast |
| `pl:item:rm:<pid>:<tok>` | remove item | same toast |

Token store: library cards reuse the existing `get_token_store` pattern
(8-hex, 300 s TTL) — a card token names a frozen page snapshot
(`{tg_id, page, pages, total, tracks}`), so edits/removes by the owner
elsewhere don't shift what a rendered card points at. The shared callback
chain (`bot/handlers/callbacks.py`) carries the action pattern, the page
pattern and `nav:library` — conversation and group 1 alike.

## 9. Phases and estimates

| Phase | Deliverable | Est. |
|---|---|---|
| **A** | Schema + models/cruds, `track_id` on TrackResult, delivery capture, `My music` v1 card + v2 menu/list, log backfill | **done 2026-11-21** (tag `music-library-phase-a` marks pre-rework state) |
| **A.2** | **UX v3 rework per `docs/music-library-ux-design.md` — §12 tasks A1–A6** (menu, shared list, detail, search, often/sort, reconciliation) | in progress 2026-11-21 |
| **B** | Playlists: create/rename/delete, add/remove item, Send-all batch task (design §8, plan §12.7) | 1–2 days |
| **C** | Mini App Music tab (grid, search, player, playlists) | 3–5 days |
| Deferred | Manual tags, usage stats, lyrics, in-chat reordering UI | revisit on demand |

Branch → live gate → confirm → merge, per the repo workflow (KB §12).

Phase A test coverage: `tests/test_28_music_library.py` (42 tests: CRUD
against the live test DB, delivery capture incl. the in-flight
metadata-only case, facade soft-failure, menu/list render, stale/owner/
private-gate, the full re-send ladder, keyboards) + updated
`tests/test_03_keyboards.py` (5-row Home, `lib:*` patterns resolvable) +
`tests/test_23_spotify_flow.py` isolating the flow suite from the capture
write.

## 10. Risks and open questions

1. **file_id lifetime** — no documented expiry for cloud Bot API files, but
   none guaranteed either. Mitigated by the ladder (§3). Live gate must
   explicitly test: re-send a track days later, after deleting the original
   message, after a process restart.
2. **Flood control on big playlists** — exact per-chat numbers are no longer in
   the docs; pacing + `retry_after` handling is the contract. Live gate: send a
   20-track playlist re-send and watch for 429s through the production proxy.
3. **Cover URLs** (Spotify CDN) fetched by Telegram server-side can go stale —
   the chat list shows no covers (rework 2026-11-21); the exposure is now
   only the Mini App grid (Phase C).
4. **Persian copy** — all new strings go through `utils/copy.py` keys from day
   one (spec §14 localization rule); Persian itself stays P1 per spec.
5. **YouTube MP3 into the library?** — deferred: no stable track id, metadata
   is weaker; library v1 is Spotify-only. `source` column leaves the door open.

## 11. Sources

- Official Bot API reference (fetched 2026-11-21 via production proxy):
  "Sending files" / "Sending by file_id" rules; `sendAudio`, `copyMessage`
  (caption + reply_markup parameters), `forwardMessage` notes.
- PTB 22.8 signatures verified locally from the project venv
  (`Bot.send_audio`, `Bot.copy_message`, `Bot.forward_message`).
- Rate-limit numeric table absent from the current docs page (verified 2026-11-21,
  incl. 2024 archive copy); flood control via 429 + `retry_after` is the
  documented mechanism.

---

## 12. UX v3 — implementation contract (approved expert design, 2026-11-21)

The expert design `docs/music-library-ux-design.md` is the UX contract.
This section is the implementation contract: what ships, in what order,
exactly how. Everything in §3 (ladder, capture, breaker) is unchanged.

### 12.1 Agreed decisions (owner, 2026-11-21)

1. **Spotify URL sent during the search prompt** → auto-cancel the search
   state and hand the link to the normal download flow (intent match),
   state returns to Home.
2. **Remove requires confirmation** (new vs v2 one-tap): the destructive
   action now sits one accidental tap behind `⋯`, confirm it.
3. **The Phase A menu ships with 4 destinations** (Latest / Search /
   Often played / Browse all); the `🎼 Playlists` row appears only when
   Phase B lands — no placeholder row (spec §10: no dead buttons).
4. **Re-sent audio keeps the source list's keyboard** (v2 behavior,
   unchanged): actions keep working from the delivered message.
5. **Playlist partial failure (Phase B)**: completion card lists the
   failed track names + `[🔁 Retry failed]` that re-sends only the
   failures.
6. **No cross-restart resume for Send all**: snapshots and the task
   registry are in-memory (P0, spec §2.5). After a restart the task is
   gone; re-tapping Send all re-sends the whole playlist. In-process
   429 pause/resume is required; cross-restart is not.

Design adaptations (deviations from the design doc, recorded here):

- **Copy/callback naming**: the design's dotted `music.*` keys and
  `music:*`/`playlist:*` payloads are mapped onto repo conventions —
  flat `LIB_*`/`PL_*` keys in `utils/copy.py`, `lib:*`/`pl:*` callback
  families (§8 reservation).
- **Open on Spotify is a URL button** (design §5.1), not the
  `music:spotify:<token>:<i>` callback its §15.2 also lists: URL
  buttons survive process restarts, never go stale, and need no
  payload.
- **Search results keyboard at >5 results**: the design's
  New-search + Previous/Next rows would make 9 rows (acceptance bar:
  ≤8). Folded into one row `[◀ Previous] [🔎 New search] [Next ▶]`.
- **Single-page lists show no page line** (resolves design §10.1
  "Page 1 of 1" vs §12.3 "don't force pagination"): the count line is
  `N tracks` when `pages == 1`, `N tracks · Page p of P` otherwise.
- **Add to playlist on the detail screen lands with Phase B** (design
  A3 lists it for A; it would be a dead button until B).

### 12.2 Screen inventory (Phase A)

Every state, exact copy (HTML) and keyboard. Keyboard row budget:
**≤ 8 rows on every screen** (acceptance bar). The escape row
`[📥 Tasks] [🏠 Home]` sits on every card that has a Menu row; the
`[📚 Menu]` button goes back to M01 (`nav:library`).

**M01 — My music menu** (the `nav:library` destination)

```
📚 <b>Your music</b>

{count} {word} saved

Everything you've received, ready to listen again.

Choose where to start:
```

- `{count}`/`{word}`: total tracks, "1 track saved" / "N tracks saved".
- Zero tracks: render the **S0 empty library** card instead.
- DB unavailable: render the **S1 unavailable** card instead.

Keyboard (Phase A = 4 destinations, decision 12.1.3; 3 rows):

```
[🕒 Latest] [🔎 Search]
[⭐ Often played] [📄 Browse all]
[📥 Tasks] [🏠 Home]
```

Phase B inserts `[🎼 Playlists]` on the second row (above the escape
row) — the row layout is designed so the addition costs no rows.

**M02 — Track list** (shared by Latest / Often played / Browse-all ×3
sorts / search results)

```
📚 <b>Your music</b> · {source label}

{count} {word}[ · Page {page} of {pages}]

1. <b>{title}</b>
   <i>{artist}</i> · {m:ss}
   Requested {once | {n} times} · Last {relative time}

2. <b>{title}</b>
   ...

Select a track to listen again or view more actions.
```

- `{source label}`: `Latest` | `Often played` | `Recently requested` |
  `Recently added` | `Title A–Z` (browse-all carries its sort in the
  heading).
- 5 tracks per page. Row = number + title (bold), then
  artist (italic) · duration, then request history. Duration omitted
  when unknown. `Requested once` vs `Requested {n} times`.
  Relative time: `just now`, `{n} min ago`, `{n} h ago`, `{n} d ago`.
- `{count} {word}` is the library/result total; the page suffix only
  when `pages > 1` (adaptation above).
- Title/artist/album HTML-escaped (DB-stored user data).

Keyboard: one row per track `[▶ n] [⋯ n]` (n = row number), then
`[◀ Previous] [Next ▶]` when `pages > 1`, then `[📚 Menu]`, then the
escape row. Max 5 + 1 + 1 + 1 = 8 rows.

**M03 — Search prompt** (library search, the conversation state)

```
🔎 Search your music

Send a track title or artist name.

You can search using part of a title or artist.

Send /cancel to stop searching.
```

Keyboard:

```
[✖ Cancel]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

While in this state: plain text = the query; a pasted media URL →
normal download flow + state ends (decision 12.1.1); `/cancel` →
toast `Search cancelled.` + back to M01.

**M04 — Browse all sort selection**

```
📄 Browse all

Choose how your library should be ordered.
```

Keyboard:

```
[🕒 Recently requested]
[📅 Recently added]
[🔤 Title A–Z]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

**M05 — Track detail** (opened from `⋯ n`)

```
🎵 Track details

<b>{title}</b>

Artist: {artist}
Album: {album}
Duration: {m:ss}
Requested: {n} times
Last requested: {relative time}
Added: {relative time}

What would you like to do?
```

- Lines omitted when the field is empty/unknown (e.g. no album).
- `Added:` = when the row was created (first delivery).

Keyboard (7 rows Phase A; 8 with Phase B's Add-to-playlist):

```
[▶ Get again]
[🎧 30s Preview]
[🎵 Open on Spotify]          ← URL button (spotify_url), no payload
[🗑 Remove from library]
[◀ Back to tracks]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

**M05b — Remove confirmation** (edits the M05 card in place)

```
🗑 Remove track?

<b>{title}</b>
{artist}

This removes the track from My music.
It does not delete previously sent messages.
```

Keyboard:

```
[🗑 Yes, remove]
[◀ Keep track]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

`Keep track` re-renders M05. `Yes, remove` deletes the row, answers
`Removed "{title}".`, and re-renders the origin list (the list the
detail was opened from — fresh read, fresh page; a now-dead page
falls back to page 1; an emptied list renders its empty state).

**M06 — Search results** (same M02 format, heading + result count)

```
🔎 Search results: {query}

Found {count} {word}[ · Page {page} of {pages}]

{M02 rows}

Select a track to listen again or view more actions.
```

Keyboard: same as M02 with `[🔎 New search]` instead of nothing —
on a multi-page result the page row becomes
`[◀ Previous] [🔎 New search] [Next ▶]` (the adaptation above keeps
8 rows). `New search` → M03 again (fresh state entry).

**M06' — No results**

```
🔎 No matching tracks

No tracks matched "{query}".

Try another title or artist name.
```

Keyboard:

```
[🔎 Search again]
[📄 Browse all]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

**M07 — Often played (empty)**

```
⭐ Often played

Your frequently requested tracks will appear here as you listen again.
```

Keyboard:

```
[🕒 Latest]
[🔎 Search]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

Non-empty Often played renders the shared M02 list (source label
`Often played`, order: `request_count` desc, `last_requested` desc).

**S0 — Empty library** (M01 when total = 0)

```
📚 <b>Your music</b>

No music saved yet.
Your tracks will appear here automatically when you receive music.
```

(The existing `LIB_NONE` copy is updated to this.)

Keyboard:

```
[🔎 Search music] [🏠 Home]
```

`Search music` → the global Spotify search input (the existing
`nav:search` destination) — in-library search has nothing to search.

**S1 — DB unavailable** (any read fails after the breaker gives up)

```
⚠ My music is temporarily unavailable.

Your library data could not be loaded.
Please try again shortly.
```

Keyboard:

```
[🔁 Try again]
[🏠 Home]
```

### 12.3 Callback inventory (v3, ≤64 bytes)

Stateless menu destinations (work from any state, before /start):

| Callback | Button | Destination | Bytes |
|---|---|---|---|
| `nav:library` | 📚 My music (Home) | M01 | 11 |
| `lib:latest` | 🕒 Latest | M02 page 1 (latest) | 10 |
| `lib:search` | 🔎 Search | M03 (+ state) | 10 |
| `lib:often` | ⭐ Often played | M02 page 1 (often) or M07 | 9 |
| `lib:browse` | 📄 Browse all | M04 | 10 |
| `lib:sort:recent` | 🕒 Recently requested | M02 page 1 (browse/recent) | 15 |
| `lib:sort:added` | 📅 Recently added | M02 page 1 (browse/added) | 14 |
| `lib:sort:az` | 🔤 Title A–Z | M02 page 1 (browse/az) | 12 |

Tokenized list actions — `T` names a frozen list-page snapshot,
`i` the 1-based row:

| Callback | Button | Stale |
|---|---|---|
| `lib:get:<T>:<i>` | ▶ i | expired toast |
| `lib:more:<T>:<i>` | ⋯ i | expired toast |
| `lib:page:<src>:<n>` (src ∈ latest\|often\|recent\|added\|az) | ◀ Previous / Next ▶ (browse-family lists) | `n > max` → page toast |
| `lib:spg:<T>:<n>` | ◀ Previous / Next ▶ (search results) | expired toast |

`lib:page` is stateless (the browse-family lists re-read the DB per
tap — no token needed); `lib:spg` is tokenized (search pages slice a
frozen result set, and the query can't ride a payload anyway).

Tokenized detail actions — `D` names a frozen detail snapshot:

| Callback | Button | Notes |
|---|---|---|
| `lib:dl:<D>` | ▶ Get again | fresh DB row read before the ladder |
| `lib:pv:<D>` | 🎧 30s Preview | |
| `lib:rm:<D>` | 🗑 Remove from library | edits card into M05b |
| `lib:rmc:<D>` | 🗑 Yes, remove | executes, back to origin list |
| `lib:keep:<D>` | ◀ Keep track | re-renders M05 |
| `lib:back:<D>` | ◀ Back to tracks | re-reads origin list from DB |
| — (URL button) | 🎵 Open on Spotify | `url=spotify_url`, no payload |

Search state:

| Trigger | Effect |
|---|---|
| `lib:search` (from M01) | edit → M03, state → LIBRARY_SEARCH |
| `lib:scx` (✖ Cancel on M03) | toast `Search cancelled.`, edit → M01, state → HOME |
| `/cancel` (in state) | same as above |
| text in state, URL | link flow, state → HOME (decision 12.1.1) |
| text in state, query | results (edit) or no-results, state → HOME |
| `lib:scn` (🔎 New search / 🔎 Search again) | edit → M03, state → LIBRARY_SEARCH |

Legacy (v2 cards still in the live chat — defensive toasts):

| Pattern | Behavior |
|---|---|
| `lib:(dl\|prev\|rm):<8hex>:(\d+)` | expired toast |
| `lib:page:(\d+)` (bare number) | expired toast |

Longest payload: `lib:spg:` + 8-hex + `:` + 3 digits = 21 bytes.
`utils/keyboards.py` keeps asserting ≤64 at button build.

### 12.4 Snapshot schema

One token store (`"library_pages"`, 8-hex tokens, 300 s TTL —
existing `utils/tokens.py`), two shapes:

```
list:   {tg_id, source, sort, query, page, pages, total, tracks}
detail: {tg_id, track_key, track, origin}
```

- `source` ∈ `latest | search | often | browse`; `sort` ∈
  `recent | added | az | None`; `query` set only for `search`.
  `tracks` = the frozen `LibraryTrack` rows of the rendered page
  (browse-family) — for `search` the FULL result set (its pages slice
  from the freeze, so result positions never shift under a rendered
  card, design §6.6).
- `origin` = `{source, sort, query, page}` — the list the detail was
  opened from. Back / remove re-read the DB under that context (a
  fresh page, fresh token), so a removed row disappears and a
  now-dead page falls back to page 1.
- Re-send/preview never act on the snapshot's row: the handler re-reads
  the track by `(tg_id, track_key)` first (fresh `file_id` if a
  parallel delivery just captured it; missing row → not-found toast).
- A re-sent audio message's keyboard is the source list's keyboard
  (decision 12.1.4): row buttons keep working from the delivered
  message; if the origin list is gone the audio carries the menu
  keyboard instead — never a dead token.

### 12.5 Data reads (single source of truth per render)

- New `cruds.library.all_tracks(db, tg_id)` — the user's rows, stable
  order. Replaces per-source SQL orderings.
- New facade `utils.library.library_page(tg_id, source, sort, query,
  page, page_size)` → `(page_tracks, total)`: one full user read on a
  worker thread, then filter/sort/paginate in Python. Rationale: the
  library is a few thousand rows max per user on a local Postgres
  (sub-ms, ~200 KB). Python `casefold()` is full Unicode casefolding
  (SQL `lower()` under the DB's `en_US.utf8` collation is
  locale-depended and misses e.g. `İ`/`ß`), and the ordering is
  deterministic and collation-independent. A SQL `ILIKE`/indexed
  variant is a later optimization, not a v3 requirement.
- New facade `utils.library.search_library(tg_id, query)` → the FULL
  matched + relevance-ordered set (the search snapshot freezes it).
- Search matching: normalize NFC + collapse whitespace + `casefold`;
  match = query substring of title, or of artist, or of
  `title + " " + artist`. Relevance score (lower wins): title prefix 0,
  title substring 1, artist prefix 2, artist substring 3,
  combined-only 4; ties by `request_count` desc, `last_requested`
  desc, title. Minimum query: 2 chars after normalization — 1 char
  re-renders the prompt with the too-short line, stays in state.
- New facade `utils.library.get_library_track(tg_id, track_key)` →
  fresh `LibraryTrack | None` (the ladder's row source).
- `read_library` (v2 page read) is retired with its call sites.

### 12.6 Search conversation state

- New state `LIBRARY_SEARCH_STATE = 6` (`utils/constants/states.py`).
- In `bot/conversations/main.py` the state's chain is the shared
  callbacks first (so `lib:*`/`nav:*` work mid-prompt), then a
  `/cancel` CommandHandler bound to the search-cancel handler (before
  the shared `/cancel` task handler — in this state `/cancel` means
  "stop searching"), then the link handler wrapped to return
  HOME_STATE (URL → download flow, decision 12.1.1), then the shared
  command handlers, then the state's TEXT handler.
- The TEXT handler: URL → link flow (HOME); <2 chars → too-short edit
  (stay in state); else run the search on a worker thread, render
  M06 or the no-results card, return HOME_STATE (result buttons then
  run on the shared chain, exactly like the Spotify search card).
- The prompt card is rendered by editing the clicked message; the
  search entry points (`lib:search`, `lib:scn`) return the state
  constant so the ConversationHandler records it. The prompt card's
  message id rides `user_data` so `/cancel` (a plain message) can edit
  it back into the menu.
- DB failure mid-search → S1 unavailable card, state → HOME.

### 12.7 Phase B scope (after the Phase A gate)

Playlists P01–P08, `pl:*` callbacks, `PL_*` copy keys:

- **B1** — Schema: `playlist` + `playlist_item` (owner-scoped,
  unique `(tg_id, name)` case-insensitive), caps (100 playlists, 50
  tracks each), CRUD, tests.
- **B2** — P01 playlist list (5/page, `[▶ {name}] [⚙ {name}]` rows,
  `pl:new` prompt flow), P02 create (name + duplicate check),
  P05 rename/delete (confirm, item cascade), tests.
- **B3** — P03 playlist detail (tracks with position, empty state,
  `[＋ Add track]` → library picker), P04 add (from the detail
  screen's picker; the detail screen's Add-to-playlist button lands
  here), P06 remove-from-playlist (one-tap, no confirm — non-
  destructive), tests.
- **B4** — P07 Send all: batch task (1 msg/s pace, the §3 ladder per
  item, in-task cancel, in-process 429 pause/resume, partial-failure
  card with failed names + `[🔁 Retry failed]` — decision 12.1.5),
  the task registry, tests.
- **B5** — Menu row `🎼 Playlists`, detail's Add-to-playlist,
  reconciliation (group gate, toasts, S0/S1 on playlist reads),
  full-suite green, live gate.

### 12.8 Phase C notes (Mini App, P2)

- The Mini App reuses the same facade reads and the same dedup key;
  covers are a Mini App concern only (chat stays cover-free).
- The `lib:*`/`pl:*` chat surface keeps working unchanged — the Mini
  App is additive.

### 12.9 Acceptance (from the design §17, as implemented)

- 5 newest: 3 taps from Home; any known track: ≤5 at any library size.
- ≤8 keyboard rows, ≤~30-char labels, no covers, 5 rows/page.
- Every state in §12.2 with exact copy; every callback owner-checked,
  private-chat-gated, ≤64 bytes.
- Remove confirms; back restores the origin list; search is
  cancel-able and its results go stale cleanly.

### 12.10 Task breakdown (Phase A)

| Task | Scope |
|---|---|
| **A1** | M01 menu (copy, keyboard with the destinations implemented so far, menu callbacks, empty/unavailable states on the new copy) + copy deck keys + tests |
| **A2** | M02 shared list: snapshot schema §12.4, `library_page` reads, numbered `▶/⋯` rows (⋯ lands in A3), Previous/Next + stateless page callbacks, legacy v2 patterns → stale toast, re-sent-audio keyboard = source list keyboard, tests |
| **A3** | M05 detail + M05b confirm: detail snapshot, `lib:dl/pv/rm/rmc/keep/back`, fresh-row ladder, remove with confirmation, origin-list restore, tests |
| **A4** | Search: state, M03 prompt, M06 results + no-results, matching/relevance §12.5, cancel paths, URL handoff, `lib:spg` paging, tests |
| **A5** | Often played (M02/M07) + Browse all (M04 + 3 sorts) + full 4-destination menu, tests |
| **A6** | State reconciliation pass: group gate on every v3 callback, expired/restart toasts, 429, preview failure, ladder exhausted, not-found, S0/S1 on every render path; full-suite green; plan doc as-built |
| **B1–B5** | Phase B (after the A6 gate): caps + case-insensitive name check, P01–P04 + prompts, membership add/remove, Send all batch (pace 1 msg/s, ladder per item, cancel, 429 pause/resume in-process, partial-failure review + Retry failed §12.1.5), regression |

Build order note: A1 ships a menu that grows (A1: Latest only — no
dead buttons); A4 adds the Search row, A5 adds Often/Browse. The
final menu is the 4-destination grid above.

### 12.11 Notes

- The v1/v2 chat surfaces are replaced in place (same `nav:library`
  entry point); v2 payloads keep a defensive stale-toast handler.
- Nothing in Phase A touches the download/search flows; the library
  read side is additive (one `all_tracks` CRUD + facade functions).
- The `playlist` schema is NOT created in Phase A (B1 owns it).

### 12.12 A6 reconciliation checklist

- Re-check every callback handler against the §12.3 table: owner
  check, private gate, toast for stale/expired/out-of-range, 429 card
  with the original payload, fresh-row ladder, S0/S1 states.
- `python -m pytest tests/` green.
- Live gate with the owner's account (64 backfilled tracks): menu →
  latest page 1/13 → ⋯ n detail → Get again (file_id relay) →
  preview → remove with confirm → search "hawk" → search paging →
  often played (13 multi-request tracks) → browse A–Z paging.
- The plan doc marked as-built.
- Each task above keeps the suite green; v3 ships as ONE commit
  (code + tests + the design doc) rather than one commit per task.

### 12.13 As-built (2026-11-22)

v3 shipped as ONE commit (code + tests + the design doc) — the A1→A6
order is the implementation narrative, not the git history. Two
refinements the implementation forced:

- **Row numbering IS the button index**: the printed number of a row
  is its 1-based position in the snapshot's `tracks` list. Search
  pages continue the numbering across pages (page 2 shows rows 6–10;
  `lib:get:T:6` resolves into the frozen FULL set); browse-family
  pages restart at 1 (their snapshot holds the page rows). This is
  what keeps a re-sent audio's keyboard honest on either family.
- Captions take the result **total** (not the page) for the
  `Found N tracks.` line, and the search caption drops the sentence
  period when the page suffix joins it.
