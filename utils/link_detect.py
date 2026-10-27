"""Media URL detection for the universal link dispatcher (plan 1.1).

A user may paste a URL (alone or wrapped in other text) from any state —
or before ever sending /start. `first_url` pulls the first URL out of a
message, `detect_media` maps it to a platform the bot knows how to
download.

Patterns are the plan's 5.1 set, fixed:
- tiktok: `vm.`/`vt.` short links actually exist (vm.tiktok.com/...)
- instagram: `stories/` carries `<username>/<story_id>`, not a bare id
- pinterest: `pinterest.com/pin/<id>` in addition to pin.it redirects
"""
import re
from urllib.parse import urlparse

# dict order = match priority (spotify before the generic ones)
MEDIA_PATTERNS = {
    "spotify": re.compile(
        r"https?://open\.spotify\.com/(track|album|playlist|artist)/[a-zA-Z0-9]+"
    ),
    "youtube": re.compile(
        r"https?://(?:www\.|m\.)?(?:"
        r"youtube\.com/(?:watch\?v=|playlist\?list=|embed/|shorts/|live/)[a-zA-Z0-9_-]+"
        r"|youtu\.be/[a-zA-Z0-9_-]+"
        r")"
    ),
    "instagram": re.compile(
        r"https?://(?:www\.)?instagram\.com/(p|reel|reels|tv|stories)/[a-zA-Z0-9_./-]+"
    ),
    "pinterest": re.compile(
        r"https?://(?:www\.)?(?:pin\.it/[a-zA-Z0-9]+|pinterest\.[a-z]{2,3}/pin/\d+)"
    ),
    "tiktok": re.compile(
        r"https?://(?:www\.)?(?:vm\.|vt\.)?tiktok\.com/[a-zA-Z0-9_/@.-]+"
    ),
}

URL_RE = re.compile(r"https?://[^\s]+")


def first_url(text: str | None) -> str | None:
    """The first URL in a message, or None. Users often wrap links in
    surrounding words; the link is what matters."""
    match = URL_RE.search(text or "")
    return match.group(0) if match else None


def detect_media(url: str) -> str | None:
    """Platform name for a known media URL, else None (e.g. google.com)."""
    for platform, pattern in MEDIA_PATTERNS.items():
        if pattern.search(url):
            return platform
    return None


def url_domain(url: str) -> str:
    """Bare domain for error messages ('open.spotify.com' etc.)."""
    return urlparse(url).netloc
