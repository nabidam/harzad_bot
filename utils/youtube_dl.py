"""Shared yt-dlp configuration.

Used by both the chat handler (bot/handlers/youtube/download.py) and the
Mini App API (web/app.py) so the two flows download identical files.
"""
from configurations.settings import (
    DOWNLOAD_MP3_PATH,
    DOWNLOAD_VIDEO_PATH,
    YT_COOKIE_FILE,
)


def video_opts(max_height: int = 720) -> dict:
    """yt-dlp options for a video download capped at `max_height`.

    mp4 only: Telegram's sendVideo demotes webm to a plain document
    (no player, no streaming, no cover/thumbnail).
    """
    return {
        'format': f'bestvideo[ext=mp4][height<={max_height}]+bestaudio[ext=m4a]/best[ext=mp4][height<={max_height}]',
        'outtmpl': DOWNLOAD_VIDEO_PATH + '%(title)s.%(ext)s',
        'cookiefile': YT_COOKIE_FILE,
        # thumbnail file next to the video, reused as the Telegram video cover
        'writethumbnail': True,
    }


def audio_opts() -> dict:
    """yt-dlp options for an audio-only download (WebApp v2 spec §4.3
    `audio_best`). m4a first (Telegram's sendAudio plays it natively),
    any audio container as the fallback — the chat's mp3 flow keeps its
    own pipeline, this one serves the app."""
    return {
        'format': 'bestaudio[ext=m4a]/bestaudio',
        'outtmpl': DOWNLOAD_MP3_PATH + '%(title)s.%(ext)s',
        'cookiefile': YT_COOKIE_FILE,
    }


# POST /api/jobs `options.target` (WebApp v2 spec §4.3): the SPA sends a
# semantic label, never a raw format string; the mapping lives HERE so
# the client cannot reach into yt-dlp's grammar. Static list (no
# per-video probing) — predictable, zero added latency.
TARGETS = {
    'video_1080': lambda: video_opts(1080),
    'video_720': lambda: video_opts(720),
    'video_480': lambda: video_opts(480),
    'video_360': lambda: video_opts(360),
    'audio_best': audio_opts,
    'default': lambda: video_opts(720),  # the v1 behavior
}


def opts_for_target(target: str | None) -> dict:
    """The yt-dlp options for one `options.target` (None = default)."""
    if target is None:
        target = 'default'
    if target not in TARGETS:
        raise KeyError(target)
    return TARGETS[target]()


YDL_OPTS = video_opts(720)
