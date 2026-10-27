"""Shared yt-dlp configuration.

Used by both the chat handler (bot/handlers/youtube/download.py) and the
Mini App API (web/app.py) so the two flows download identical files.
"""
from configurations.settings import DOWNLOAD_VIDEO_PATH, YT_COOKIE_FILE


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


YDL_OPTS = video_opts(720)
