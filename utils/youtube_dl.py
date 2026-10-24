"""Shared yt-dlp configuration.

Used by both the chat handler (bot/handlers/youtube/download.py) and the
Mini App API (web/app.py) so the two flows download identical files.
"""
from configurations.settings import DOWNLOAD_VIDEO_PATH, YT_COOKIE_FILE

# mp4 only: Telegram's sendVideo demotes webm to a plain document
# (no player, no streaming, no cover/thumbnail)
YDL_OPTS = {
    'format': 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]',
    'outtmpl': DOWNLOAD_VIDEO_PATH + '%(title)s.%(ext)s',
    'cookiefile': YT_COOKIE_FILE,
    # thumbnail file next to the video, reused as the Telegram video cover
    'writethumbnail': True,
}
