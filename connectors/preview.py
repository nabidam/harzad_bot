"""30-second audio preview resolution (plan 1.4).

The free Spotify backend (ytmusicapi) serves no `preview_url` — that
field is Spotify-licensed. Previews come from public catalog APIs
instead: iTunes Search first, Deezer as fallback. Both return a
30-second MP3 URL that Telegram can fetch directly (send_audio with a
URL), so no file staging is involved.
"""
import asyncio
import re
from logging import getLogger
from typing import Optional

import requests

logger = getLogger(__name__)

_REQUEST_TIMEOUT = 15.0


def _norm(text: str) -> str:
    """Lowercase alphanumeric-only form for fuzzy title matching."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _title_match(title: str, candidate: str) -> bool:
    """Is `candidate` plausibly the requested `title`?

    Symmetric containment: 'Starboy' matches 'Starboy (Remix)' and
    vice versa, so a loose user query still lines up with the catalog
    spelling while an unrelated top hit is rejected.
    """
    t, c = _norm(title), _norm(candidate)
    return bool(t) and bool(c) and (t in c or c in t)


def _itunes_preview(title: str, artist: str) -> Optional[str]:
    resp = requests.get(
        "https://itunes.apple.com/search",
        params={
            "term": " ".join(p for p in (title, artist) if p),
            "media": "music",
            "limit": 5,
        },
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    for item in resp.json().get("results", []):
        if _title_match(title, item.get("trackName") or ""):
            return item.get("previewUrl") or None
    return None


def _deezer_preview(title: str, artist: str) -> Optional[str]:
    resp = requests.get(
        "https://api.deezer.com/search",
        params={
            "q": " ".join(p for p in (title, artist) if p),
            "limit": 5,
        },
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    for item in resp.json().get("data", []):
        if _title_match(title, item.get("title") or ""):
            return item.get("preview") or None
    return None


def resolve_preview_url_sync(title: str, artist: str) -> Optional[str]:
    """A 30s MP3 URL for the track, or None.

    iTunes first, Deezer as fallback. A fetcher that errors (network,
    HTTP status) is skipped rather than fatal — the preview is a
    nicety, and the other source may still work.
    """
    for fetcher in (_itunes_preview, _deezer_preview):
        try:
            url = fetcher(title, artist)
        except requests.RequestException as err:
            logger.warning("preview: %s failed: %s", fetcher.__name__, err)
            continue
        if url:
            return url
    return None


async def resolve_preview_url(title: str, artist: str) -> Optional[str]:
    """resolve_preview_url_sync offloaded to an executor (blocking I/O)."""
    return await asyncio.to_thread(resolve_preview_url_sync, title, artist)
