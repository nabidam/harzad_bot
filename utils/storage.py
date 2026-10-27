"""Disk lifecycle for downloaded media (plan 1.5).

Files in the download folders are temporary staging: they exist so the
bot can measure size, read the duration and upload to Telegram. Once an
upload succeeds the file is deleted immediately; the periodic garbage
collector is the safety net for what crashes leave behind, and for the
>50 MB files that are kept on purpose (served over the web link).
"""
import asyncio
import os
import time
from logging import getLogger

from configurations.settings import DOWNLOAD_MP3_PATH, DOWNLOAD_VIDEO_PATH

logger = getLogger(__name__)

# How long a file may sit in the download folders before the garbage
# collector removes it. Files in the >50 MB web-link fallback branch are
# included: that link only points at this disk, so a week-old mp4 is
# stale either way.
GC_MAX_AGE_SECONDS = 2 * 60 * 60
# Run the collector once an hour.
GC_INTERVAL_SECONDS = 60 * 60

# yt-dlp writes the thumbnail next to the media when writethumbnail is
# on (YouTube currently serves .webp); it is deleted with the media.
_THUMBNAIL_EXTS = (".jpg", ".jpeg", ".webp", ".png")


def media_and_cover(filename: str):
    """Yield the media path plus any adjacent thumbnail files."""
    yield filename
    base = os.path.splitext(filename)[0]
    for ext in _THUMBNAIL_EXTS:
        yield base + ext


def cleanup_media(filename: str) -> None:
    """Best-effort deletion of a delivered file and its cover.

    A leftover file is never fatal — the GC pass retries it later.
    """
    for path in media_and_cover(filename):
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            logger.info("cleaned up %s", path)
        except OSError:
            logger.warning("could not delete %s", path, exc_info=True)


def gc_old_files(roots=None, max_age_seconds: float = GC_MAX_AGE_SECONDS) -> int:
    """Delete files older than `max_age_seconds` under the download roots.

    Returns the number of files removed. Recently modified files (a
    download in flight) are skipped.
    """
    if roots is None:
        roots = (DOWNLOAD_MP3_PATH, DOWNLOAD_VIDEO_PATH)
    now = time.time()
    removed = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in os.scandir(root):
            if not entry.is_file():
                continue
            try:
                age = now - entry.stat().st_mtime
            except OSError:
                continue
            if age > max_age_seconds:
                try:
                    os.remove(entry.path)
                    removed += 1
                    logger.info("GC removed %s (age %.1fh)", entry.path, age / 3600)
                except OSError:
                    logger.warning("GC: could not remove %s", entry.path, exc_info=True)
    if removed:
        logger.info("GC removed %d stale file(s)", removed)
    return removed


async def gc_loop(interval_seconds: float = GC_INTERVAL_SECONDS) -> None:
    """Background garbage collection, started at boot.

    Plain asyncio task instead of PTB's JobQueue: APScheduler is not
    installed (python-telegram-bot's [job-queue] extra is not a
    dependency), and a while-sleep loop has no reason to be anything
    fancier.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            gc_old_files()
        except Exception:
            logger.warning("GC pass failed", exc_info=True)
