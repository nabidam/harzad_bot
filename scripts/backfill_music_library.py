"""One-shot backfill: import a user's past Spotify requests into the
music library (docs/music-library-plan.md §4).

Run from the repo root with the bot's environment (.env):

    .venv/bin/python scripts/backfill_music_library.py --tg-id 575570675
    .venv/bin/python scripts/backfill_music_library.py --dry-run
    .venv/bin/python scripts/backfill_music_library.py --limit 3   # quick check

Where the data comes from: every pasted message is in the `logs` table
(tg_id, message, created_on), so all pasted Spotify track URLs — with
how often each was pasted and when first/last — are recoverable. The
Bot API has **no chat-history endpoint**, though: the originally
delivered audio messages (and their Telegram file_ids) are
unreachable. Backfilled rows are therefore metadata-only
(`file_id`/`orig_*` = None) — "Get again" on one walks the re-send
ladder straight to the re-download step until a fresh delivery
captures cloud handles. The metadata itself (title, artist, album,
duration, cover) is resolved through the same spotdl
URL-resolve path the production URL flow uses.

Semantics (all idempotent — safe to re-run):

- insert-if-absent per (tg_id, spotify track id); a row captured by a
  live delivery (file refs, true counts) is never touched
- `request_count` / `added_on` / `last_requested` come from the log
  rows (real history, not "now")
- a track whose resolve fails is skipped and reported — re-run the
  script to retry it
- non-track URLs (albums/playlists) are reported, not expanded
"""
import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# runnable as a plain script from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cruds.library as cruds
from connectors.db import SessionLocal
from sqlalchemy import text

TRACK_URL_RE = re.compile(r"open\.spotify\.com/track/([A-Za-z0-9]+)")
SPOTIFY_URL_RE = re.compile(r"open\.spotify\.com/(?!track/)\S+")

# ONE shared instance: spotdl's SpotifyClient is a process-wide
# singleton (second init raises), and the bot's production pattern is a
# single Spotdl shared across worker threads (concurrent searches
# happen there too).
_shared_spotdl = None


def _spotdl():
    global _shared_spotdl
    if _shared_spotdl is None:
        from connectors.spotdl import create_spotdl_instance
        _shared_spotdl = create_spotdl_instance()
    return _shared_spotdl


def _resolve_one(track_id: str, url: str):
    """(metadata dict | None, error str | None, seconds)."""
    t0 = time.monotonic()
    try:
        songs = _spotdl().search([url])
    except Exception as err:
        return None, f"{type(err).__name__}: {err}", time.monotonic() - t0
    if not songs:
        return None, "no result", time.monotonic() - t0
    s = songs[0]
    return dict(
        title=(getattr(s, "name", None) or track_id).strip(),
        artist=getattr(s, "artist", None) or "",
        album=getattr(s, "album_name", None) or "",
        duration_sec=getattr(s, "duration", None),
        cover_url=getattr(s, "cover_url", None),
    ), None, time.monotonic() - t0


def collect_requests(db, tg_id: int):
    """Log rows -> {track_id: {url, count, first, last}} + ignored URLs."""
    rows = db.execute(
        text("select created_on, message from logs where tg_id = :t"),
        {"t": tg_id},
    ).fetchall()
    requests: dict[str, dict] = {}
    ignored = []
    for ts, message in rows:
        message = message or ""
        m = TRACK_URL_RE.search(message)
        if m:
            tid = m.group(1)
            entry = requests.setdefault(
                tid,
                {"url": f"https://open.spotify.com/track/{tid}",
                 "count": 0, "first": ts, "last": ts},
            )
            entry["count"] += 1
            entry["first"] = min(entry["first"], ts)
            entry["last"] = max(entry["last"], ts)
        elif SPOTIFY_URL_RE.search(message):
            ignored.append(message.strip())
    return requests, ignored


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tg-id", type=int, default=575570675,
                        help="user whose logs to import (default: owner)")
    parser.add_argument("--workers", type=int, default=5,
                        help="parallel URL resolves (default 5)")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="per-track resolve timeout, seconds")
    parser.add_argument("--limit", type=int, default=0,
                        help="only process the first N missing tracks (0 = all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be imported; no resolves, no writes")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        requests, ignored = collect_requests(db, args.tg_id)
        if not requests:
            print(f"no Spotify track URLs in logs for tg_id {args.tg_id}")
            return 0

        existing = {
            r[0]
            for r in db.execute(
                text("select track_key from tracks where tg_id = :t"),
                {"t": args.tg_id},
            )
        }
        skipped = sorted(t for t in requests if t in existing)
        todo = [t for t in requests if t not in existing]
        # newest-pasted first, so a --limit run covers the recent tracks
        todo.sort(key=lambda t: requests[t]["last"], reverse=True)
        if args.limit:
            todo = todo[: args.limit]

        print(f"tg_id {args.tg_id}: {len(requests)} distinct tracks in logs")
        print(f"  already in library : {len(skipped)} (untouched)")
        print(f"  to backfill        : {len(todo)}")
        if ignored:
            print(f"  non-track URLs     : {len(ignored)} (ignored)")
            for url in ignored[:3]:
                print(f"    {url}")
        if not todo:
            print("nothing to do")
            return 0

        if args.dry_run:
            for tid in todo:
                e = requests[tid]
                print(f"  {tid}  x{e['count']}  last {e['last']}  {e['url']}")
            print("dry run — no resolves, no writes")
            return 0

        print(f"resolving {len(todo)} tracks with {args.workers} workers "
              "(expect a few minutes — the Spotify API is slow from this box)")
        _spotdl()  # initialize the singleton on the main thread
        results: dict[str, object] = {}
        timed_out: list[str] = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(_resolve_one, tid, requests[tid]["url"]): tid
                for tid in todo
            }
            done = 0
            for future, tid in futures.items():
                try:
                    results[tid] = future.result(timeout=args.timeout)
                except Exception as err:
                    results[tid] = (None, f"timeout after {args.timeout:.0f}s: "
                                          f"{type(err).__name__}", 0.0)
                    timed_out.append(tid)
                done += 1
                status, err, secs = results[tid]
                title = status.get("title") if status else "-"
                print(f"  [{done}/{len(todo)}] {tid}  "
                      f"{'ok' if status else 'FAILED'}  {secs:5.1f}s  {title}")

        backfilled, failed = 0, []
        for tid in todo:
            status, err, _secs = results[tid]
            if status is None:
                failed.append((tid, err))
                continue
            e = requests[tid]
            cruds.backfill_track(
                db, tg_id=args.tg_id, track_key=tid,
                spotify_url=e["url"],
                request_count=e["count"],
                added_on=e["first"], last_requested=e["last"],
                **status,
            )
            backfilled += 1

        total = cruds.count_tracks(db, args.tg_id)
        print(f"\nbackfilled {backfilled}, skipped {len(skipped)} "
              f"(already in library), failed {len(failed)}")
        for tid, err in failed:
            print(f"  FAILED {tid}: {err}")
        print(f"library now holds {total} tracks for tg_id {args.tg_id}")
        print("note: backfilled rows have no Telegram file refs — their "
              "'Get again' re-downloads until a fresh delivery is captured")
        exit_code = 0 if not failed else 1
        if timed_out:
            # non-daemon worker threads from timed-out resolves would hold
            # the interpreter open on exit; all results are already written
            sys.stdout.flush()
            os._exit(exit_code)
        return exit_code
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
