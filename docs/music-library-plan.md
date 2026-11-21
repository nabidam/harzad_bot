# Harzad Bot — Music Library Plan

**Date:** 2026-11-21
**Status:** Agreed direction (owner). Phase A implemented 2026-11-21 on branch
`music-library` — awaiting the live gate, then merge `--no-ff` per repo workflow.
**Scope:** per-user music library, re-delivery of previously requested tracks, playlists
**Related:** `HARZAD_UX_REDESIGN_SPEC.md` (P1 "persistent job history/favorites", P2 "Mini App media hub"), `PRODUCT.md` §8 (persistence = medium cost)

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

## 5. Chat UX (Phase A — implemented)

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

**Library view:** one editable card, reusing the search-result carousel
pattern (spec §5.3 — tokenized, 300 s TTL store, owner-checked, stale
toast). The card shows one track, "Track {i} of {n}", over the user's
last-requested tracks, page of 10.

Caption (HTML, escaped):

> 🎵 <b>{title}</b>
> <i>{artist}</i>
> 📀 {album} · 📥 requested {request_count}× · last {relative time, "2 d ago"}
> Track {i} of {n}

Keyboard (Phase A — `➕ Add to playlist` lands in Phase B on row 2; no dead
buttons before its handler exists, spec §10):

| Row | Buttons | Callback |
|---|---|---|
| 1 | `⬇️ Get again` (primary) · `🎧 30s Preview` | `lib:dl:<token>:<i>` · `lib:prev:<token>:<i>` |
| 2 | `🗑 Remove` | `lib:rm:<token>:<i>` |
| 3 | `◀ Previous` · `Next ▶` (page has >1 track) | `lib:prevcard:<token>:<i>` · `lib:next:<token>:<i>` |
| 4 | `◀ Prev page` · `Next page ▶` (>1 page) | `lib:page:<n>` |
| 5 | `📥 Tasks` · `🏠 Home` | `nav:tasks` · `nav:home` |

`<i>` is the 1-based position inside the token's frozen page snapshot —
the search card's index-in-payload pattern, so two rendered cards from the
same snapshot never point at each other. All ≤64 bytes (longest:
`lib:prevcard:` + 8-hex token + `:` + 2 digits = 23).

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
- **Carousel boundaries** → `Already showing the newest track.` /
  `That's the oldest track on this page.`
- **429 on a re-send** → visible wait message carrying the `retry_after`
  seconds (the tap is still there); not a toast, since the early ack already
  went out.
- **Ladder exhausted** → message `I can't re-fetch this track anymore — …
  Search for it again to get it back.` + `[📥 Tasks] [🏠 Home]` (error
  class 2, spec §7) — never a silent skip.

## 6. Playlists (Phase B)

- `📃 Playlists` button on the library card row (and Home, under My music).
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

Phase A (implemented):

| Callback | Button | Stale behavior |
|---|---|---|
| `nav:library` | 📚 My music | stateless — never stale (group: private toast) |
| `lib:dl:<token>:<i>` | ⬇️ Get again | toast `This list expired. Open My music again.` |
| `lib:prev:<token>:<i>` | 🎧 30s Preview | same toast |
| `lib:rm:<token>:<i>` | 🗑 Remove | same toast |
| `lib:prevcard:<token>:<i>` / `lib:next:<token>:<i>` | ◀ / ▶ | same toast; boundaries get first/last toasts |
| `lib:page:<n>` | page nav | `n < 1` → page 1; `n > max` → toast `That page doesn't exist anymore.` |

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
| **A** | Schema + models/cruds, `track_id` on TrackResult, delivery capture, `My music` card (re-send + preview + remove) | 1–2 days — **done 2026-11-21**, branch `music-library`, live gate pending |
| **B** | Playlists: create/rename/delete, add/remove item, Send-all batch task | 1–2 days |
| **C** | Mini App Music tab (grid, search, player, playlists) | 3–5 days |
| Deferred | Manual tags, usage stats, lyrics, in-chat reordering UI | revisit on demand |

Branch → live gate → confirm → merge, per the repo workflow (KB §12).

Phase A test coverage: `tests/test_28_music_library.py` (CRUD against the
live test DB, delivery capture incl. the in-flight metadata-only case,
facade soft-failure, card render/stale/owner/private-gate, the full re-send
ladder, keyboards) + updated `tests/test_03_keyboards.py` (5-row Home,
`lib:*` patterns resolvable) + `tests/test_23_spotify_flow.py` isolating the
flow suite from the capture write.

## 10. Risks and open questions

1. **file_id lifetime** — no documented expiry for cloud Bot API files, but
   none guaranteed either. Mitigated by the ladder (§3). Live gate must
   explicitly test: re-send a track days later, after deleting the original
   message, after a process restart.
2. **Flood control on big playlists** — exact per-chat numbers are no longer in
   the docs; pacing + `retry_after` handling is the contract. Live gate: send a
   20-track playlist re-send and watch for 429s through the production proxy.
3. **Cover URLs** (Spotify CDN) fetched by Telegram server-side can go stale —
   library card falls back to text card (existing search-card property).
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
