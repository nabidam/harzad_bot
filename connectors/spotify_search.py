"""Spotify track search for the music discovery feature (plan 1.2).

Uses the same `SpotifyClient` that spotdl initializes at boot (the
free/ytmusicapi backend with the bot's own client credentials — the
official API's search endpoint is not usable with this app). One raw
search call returns up to 100 ranked track items; we keep the top few.

The free backend items carry no `preview_url` (a Spotify-licensed
field), so 30s previews come from the iTunes fallback (plan 1.4).
"""
import time
from dataclasses import dataclass
from logging import getLogger
from typing import Optional

from utils.executor import DetachedWorker

logger = getLogger(__name__)

# Plan 1.2: "top 3-5 candidate matches" — 3 keeps the chat digestible.
MAX_RESULTS = 3
# Plan 5.3: results cached in memory for 300s.
CACHE_TTL_SECONDS = 300


@dataclass
class TrackResult:
    """Plan 5.2 return schema."""

    title: str
    artist: str
    album: str
    duration_sec: int
    spotify_url: str
    cover_url: Optional[str]
    preview_url: Optional[str]


# query(lower) -> (monotonic timestamp, results)
_cache: dict[str, tuple[float, list[TrackResult]]] = {}


def _raw_search(term: str) -> list[TrackResult]:
    """One raw Spotify search (sync; run in an executor)."""
    from spotdl.utils.spotify import SpotifyClient

    raw = SpotifyClient().search(term) or {}
    items = raw.get("tracks", {}).get("items", [])
    results: list[TrackResult] = []
    for item in items[:MAX_RESULTS]:
        album = item.get("album") or {}
        images = album.get("images") or []
        cover = None
        if images:
            largest = max(images, key=lambda i: (i.get("width") or 0) * (i.get("height") or 0))
            cover = largest.get("url")
        results.append(TrackResult(
            title=item.get("name") or "",
            artist=", ".join(a.get("name") or "" for a in item.get("artists", [])),
            album=album.get("name") or "",
            duration_sec=int(item.get("duration_ms") or 0) // 1000,
            spotify_url=(item.get("external_urls") or {}).get("spotify") or "",
            cover_url=cover,
            preview_url=item.get("preview_url"),
        ))
    return results


async def search_tracks(term: str) -> list[TrackResult]:
    """Top track candidates for a plain-text query, cached 300s in memory.

    Raises on API/network failure — the caller decides the user-facing
    error message.
    """
    term = term.strip()
    key = term.lower()
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    # Detached worker (utils.executor): a cancel mid-search must not
    # join the worker on the loop thread (a slow free-API search would
    # freeze the bot for its remainder).
    worker = DetachedWorker(_raw_search, term)
    results = await worker.fut
    _cache[key] = (now, results)
    return results


def format_duration(total_seconds: int) -> str:
    """355 -> '5:55' for card captions."""
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"
