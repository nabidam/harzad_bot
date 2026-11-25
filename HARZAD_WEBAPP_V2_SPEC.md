# Harzad Web App v2 — Spec

Status: draft for owner review. Extends `HARZAD_UX_REDESIGN_SPEC.md` (v1 §12
Mini App contract stays the source of truth for copy and states).

## 1. Goals

1. **My music → professional music library.** Full playback with queue,
   playlists, and tags — the standard feature set of a simple music library.
2. **Download tab → advanced options.** Provider-aware options (YouTube:
   format/resolution; others: sensible default), chosen per request.
3. **Frontend migration: React 18 + TypeScript + Vite.** Decided 2026-11-25
   before feature work (see §6 for why the vanilla single file stops scaling
   at exactly this feature set).
4. **Build in GitHub Actions.** Production deploys consume a `dist` artifact;
   the VPS (`nl`) stays node-free.

Non-goals (v2): multi-device sync, lyrics, gapless playback, offline
downloads for the web client, social features.

## 2. Hard constraints (unchanged from v1)

- aiohttp serves the SPA at `/`; nginx proxies `harzad.navidam.ir` →
  `127.0.0.1:8377`. No nginx changes.
- Every library call carries `initData` (query on GET, JSON body on POST).
  Anonymous browser mode supported; library is TG-only.
- **Metadata-only storage.** `utils/library.py`: "no media bytes ever touch
  this box (root fs is nearly full; the whole architecture is
  staging-delete)." v2 must not store media locally, ever.
- Canonical copy deck (spec §14) stays byte-pinned by tests.

## 3. Storage reality → playback architecture

Track bytes exist **only on Telegram's servers**, referenced by the per-row
`file_id` (captured at delivery; present for authenticated jobs under the
50 MB bot-upload cap). The 30s "preview" is an iTunes/Deezer catalog MP3
URL (`connectors/preview.py`) — client-side playable, no proxying.

**Full playback = Range proxy to the Telegram file:**

```
GET /api/library/track/{track_key}/stream?initData=...
    auth via initData (same as all library routes)
    row = get_library_track(tg_id, key)          (existing facade)
    if row.file_id is None: 404 + {error: "no-source"}   → UI: preview fallback
    file_path = bot.get_file(file_id).file_path   (in-memory cache, ~1h TTL)
    upstream = https://api.telegram.org/file/bot<TOKEN>/<file_path>
    proxy with Range pass-through:
        client `Range: bytes=A-B` → upstream Range → 206/200
        forward Content-Range / Content-Length / Content-Type
    no local caching (bandwidth-only cost, consistent with staging-delete)
```

Notes:
- The token never reaches the client (the Telegram file URL embeds it), so
  proxying is mandatory; direct URLs are not an option.
- Cost model: VPS egress per play ≈ file size (~3–6 MB per 3-min m4a).
  Personal-scale use; acceptable — owner confirmed direction.
- `file_id` missing (anonymous-origin rows, >50 MB files) → track stays
  playable as 30s preview with an honest "full playback unavailable" state.
  The re-send action (existing) can restore a fresh `file_id`.
- Spike before building the player UI: verify (a) Telegram file endpoint
  honors `Range`, (b) Telegram webview audio behavior — background playback
  policy, media keys, `MediaSession` support. 1 day.

## 4. Backend changes

### 4.1 Schema (alembic migration, all user-scoped)

```
playlists(id PK, user_id, name, created_at, updated_at)
  UNIQUE(user_id, lower(name))
playlist_tracks(playlist_id FK, track_key, position INT, added_at,
  PK(playlist_id, track_key))
tags(id PK, user_id, name, UNIQUE(user_id, lower(name)))
track_tags(track_key, tag_id, PK(track_key, tag_id))
```

`track_key` references the library row; orphan rows (track removed) are
pruned by the existing removal path (`remove_track_sync` extends to cascade
playlist/tag links).

### 4.2 New endpoints (all `initData`-authenticated, same error contract)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/library/track/{key}/stream` | Range proxy (above) |
| GET | `/api/library/playlists` | List playlists + track counts |
| POST | `/api/library/playlists` | `{name}` create (409 on dup name) |
| PATCH | `/api/library/playlists/{id}` | `{name?}` rename |
| DELETE | `/api/library/playlists/{id}` | Delete (keeps tracks) |
| GET | `/api/library/playlists/{id}/tracks?page=` | Paged tracks (library row shape) |
| POST | `/api/library/playlists/{id}/tracks` | `{track_keys: []}` add (idempotent) |
| DELETE | `/api/library/playlists/{id}/tracks/{key}` | Remove one |
| GET | `/api/library/tags` | List tags + counts |
| POST | `/api/library/tags` | `{name}` create |
| DELETE | `/api/library/tags/{id}` | Delete tag + links |
| POST | `/api/library/track/{key}/tags` | `{names: []}` set/merge tag links |
| GET | `/api/library?tag={name}` | Filter existing library views by tag |

### 4.3 Download job options

`POST /api/jobs` accepts an optional `options` object; the job row echoes it
so the UI can show what was requested.

- YouTube (provider detected from URL, as today):
  `target` ∈ `video_1080 | video_720 | video_480 | video_360 | audio_best |
  default` — mapped server-side to yt-dlp format selectors
  (`bv*[height<=1080]+ba/b`, `bestaudio[ext=m4a]/bestaudio`, …).
  v1 uses a **static option list** (no per-video probing) — predictable,
  zero added latency; probing is a candidate for later.
- Instagram / TikTok / Pinterest / Spotify: no options (server picks best,
  as today). UI hides the option row for these providers.
- Validation server-side; unknown target → 400. Frontend never passes
  raw format strings.

## 5. Frontend architecture

### 5.1 Layout

```
web/frontend/            # Vite + React 18 + TS (new)
  src/
    api/client.ts        # the ONLY fetch site; injects initData (q/body)
    api/types.ts         # typed contracts (jobs, library, playlists, tags)
    state/player.ts      # Zustand: current, queue, position, repeat/shuffle
    state/library.ts     # view, filters, tags, selection
    state/jobs.ts        # job polling state machine (moved from v1)
    components/…         # tabs, grid, sheet, player, now-playing, chips
  → vite build → dist/ → web/static/ (CI artifact; index.html + hashed assets)
```

- **`api/client.ts` is the only network call site.** The initData-401
  regression class (test_29) becomes structurally impossible: one function,
  one injection point, unit-tested.
- **Player state:** one `HTMLAudioElement` singleton (module scope),
  position from `timeupdate` (rAF-throttled), `MediaSession` metadata +
  action handlers, seek via Range GETs (server-side, transparent).
- **Theme:** port v1 token system verbatim (CSS custom properties,
  `prefers-color-scheme` outside TG + `themeParams` luminance inside).
- **Icons:** Tabler set ported from the v1 `ICONS` map (same 25 icons,
  inline; bundler allows the npm package but inline keeps the asset story
  identical).
- **Persistence:** `localStorage` only for last-used download options and
  last open tab. No auth state, no media, ever.

### 5.2 Feature list (v1)

Playback:
- Full-track stream when `file_id` exists; 30s preview fallback with
  honest state otherwise.
- Persistent mini player across tabs (v1 behavior, kept).
- Now Playing sheet: cover, title/artist, seek bar, position/duration,
  prev/next, shuffle, repeat (off/all/one), close.
- Queue: current context (library view / playlist) becomes the queue;
  prev/next in-order; shuffle = seeded permutation; repeat modes.
- Media Session (lock-screen controls) where the webview allows.

Library:
- Existing views (latest/daily/search/often/browse) + tag filter chips.
- Tags: create (inline), assign via track sheet, delete, count badges.
- Playlists: list view; create/rename/delete; add tracks (track sheet
  "Add to playlist…" + long-press multi-select on grid); playlist page
  with cover (first track) and paged tracks.
  v1: add/remove only — **no drag-reorder**.

Download:
- Option row appears for YouTube URLs: quality chips (1080/720/480/360,
  audio, default) — selection sent as `options.target`, remembered per
  provider in localStorage.
- Job card shows the selected target while polling.

## 6. Why migrate now (recorded decision)

v1 is 1641 lines (≈1022 JS) single file. The v2 feature set adds three
concurrent state machines (player/queue, library selection+filters,
job-options) and CRUD-heavy views. Past that line, vanilla DOM bookkeeping
costs more per feature than a component model saves. Migration happens
**before** feature work so no feature is built twice.

## 7. Deploy pipeline

Decision correction (2026-11-25, Phase 0 start): the VPS has no GitHub
token, and adding a PAT just for artifact downloads is a new secret on a
box that already trusts SSH. The build is delivered as a **branch**
instead — no credentials anywhere on the VPS.

1. **GitHub Actions** (`.github/workflows/webapp.yml`): on push to `main` —
   `npm ci && npm run typecheck && npm test && vite build` (node 20). The
   build is **single-file**: `vite-plugin-singlefile` inlines all JS/CSS
   into one `web/static/index.html` (~200 kB), so the existing
   `web/app.py` route serves it unchanged and the deploy surface is exactly
   one file. The commit publishes the built file on branch `webapp-dist`
   (main + one commit; force-pushed; uses the built-in `GITHUB_TOKEN` —
   the only credential involved). The built file is stashed in
   `$RUNNER_TEMP` before `git checkout -f`, because checkout would discard
   it.
2. **`scripts/deploy.sh`** (VPS): after `git reset --hard origin/main`,
   `git fetch origin webapp-dist`, `git archive` the static tree into a
   temp dir, validate `index.html` exists, then `rsync -a --delete` into
   `web/static/`. If any step fails: previous `web/static` contents stay
   intact (never serve a half-replaced static dir). Existing
   uv/alembic/systemd steps unchanged.
3. No node on the VPS, no secrets on the VPS.
4. `web/static/` in the repo holds the last committed build snapshot so
   local dev / test runs never need node; the VPS overwrites it with the
   same-commit build at deploy time.

## 8. Test strategy rework

- `test_index_serves_spa` byte-equality **stays**: the build is a single
  inlined `web/static/index.html` (vite-plugin-singlefile), committed as
  a snapshot; `web/app.py` serves that exact file, so served bytes ==
  committed file.
- Pinned-copy tests (test_08/test_29) repoint from the single HTML file
  to `web/frontend/src/**` — same strings, asserted across the joined
  TSX source tree. JSX markup pins (`>Download<`,
  `data-view="daily">Daily<`) only exist pre-compile, so the source tree
  is the pin surface; deck strings live in `src/copy.ts` for greppability.
- initData wiring regex tests **replaced** by vitest unit tests of
  `api/client.ts` (GET→query via `withInit()`, POST→body, 401→`no-auth`
  signal) + a static centralization check (both sides: vitest
  `no-fetch.test.ts` and pytest `test_miniapp_music_sends_init_data`)
  that no module outside `api/client.ts` calls `fetch`.
- New vitest suites: queue operations (shuffle seed, repeat modes,
  next/prev at boundaries), download-options mapping.
- New pytest: stream endpoint (Range 206/200 pass-through, 404 no-source,
  401), playlist/tag CRUD + cascade prune, job options validation.

## 9. Phases

| # | Scope | Exit bar |
|---|---|---|
| 0 | 1:1 React/TS port of existing 3 tabs + CI + deploy.sh artifact step + test rework. **Zero new features.** | Pinned tests green against new tree; production serves the built app byte-identical behavior; visual parity dark+light |
| 1 | Streaming backbone spike (Range + webview audio) → `stream` endpoint → persistent player + Now Playing + queue/shuffle/repeat + Media Session | Full-track seekable playback in TG webview for `file_id` tracks; preview fallback honest |
| 2 | Playlists + tags: schema, endpoints, UI | CRUD verified by pytest + e2e in TG webview |
| 3 | Download options: job `options`, provider matrix UI | YouTube quality selection round-trips into the job; others unchanged |

Phase 1 spike gates the rest of Phase 1: if the webview kills background
audio or Telegram file Range fails, playback UX re-scopes (e.g.,
Telegram-native audio objects) before more UI is built.

## 10. Open questions (owner)

1. VPS egress per play via the stream proxy — confirmed acceptable?
   (Direction was; cost is ≈ one file size per full play.)
2. Playlist **reorder** (drag) — excluded from v1; fine?
3. Static quality list for YouTube v1 (no per-video probe) — fine?
4. Track sheet tag editing: inline rename of existing tags, or assign-only
   in v1? (Recommend assign-only; tag edit = type + create.)
