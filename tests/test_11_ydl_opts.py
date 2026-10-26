"""#11 utils.youtube_dl.YDL_OPTS — shape + real downloads.

The live tests hit YouTube through the box proxy; run with `pytest -m live`.
"""
import glob
import os
import shutil
import time

import pytest
from mutagen import File as MutagenFile

from configurations.settings import DOWNLOAD_MP3_PATH, DOWNLOAD_VIDEO_PATH, YT_COOKIE_FILE
from utils.youtube_dl import YDL_OPTS

VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", 19 s


# --- shape --------------------------------------------------------------------

def test_ydl_opts_mp4_only_720p_cap():
    fmt = YDL_OPTS["format"]
    assert "ext=mp4" in fmt
    assert "height<=720" in fmt
    # no webm: Telegram demotes webm videos to plain documents
    assert "webm" not in fmt


def test_ydl_opts_outtmpl_concatenates_path():
    # Known quirk: DOWNLOAD_VIDEO_PATH has no trailing slash and the template
    # is string-concatenated, so files land as "<path><title>.<ext>" directly
    # in the parent directory.
    assert YDL_OPTS["outtmpl"] == DOWNLOAD_VIDEO_PATH + "%(title)s.%(ext)s"


def test_ydl_opts_writethumbnail_and_cookies():
    assert YDL_OPTS["writethumbnail"] is True
    assert YDL_OPTS["cookiefile"] == YT_COOKIE_FILE
    assert os.path.isfile(YT_COOKIE_FILE), f"cookie file missing: {YT_COOKIE_FILE}"


# --- live -----------------------------------------------------------------------

@pytest.fixture
def proxy_env(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10808")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10808")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")


@pytest.mark.live
def test_live_youtube_video_download(proxy_env, tmp_path):
    from yt_dlp import YoutubeDL
    from utils.helpers import prepare_cover

    with YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(VIDEO_URL, download=False)
        assert info.get("title")
        ydl.download([VIDEO_URL])
        filename = ydl.prepare_filename(info)

    created = [filename]
    try:
        assert os.path.isfile(filename), filename
        assert filename.endswith(".mp4")
        assert os.path.getsize(filename) > 50_000
        # duration via mutagen (the moviepy replacement)
        duration = int(MutagenFile(filename).info.length)
        assert 15 < duration < 30
        # the web thumbnail written next to the video must become a cover
        base = os.path.splitext(filename)[0]
        for ext in (".jpg", ".jpeg", ".webp", ".png"):
            if os.path.isfile(base + ext):
                created.append(base + ext)
        cover = prepare_cover(filename)
        assert cover is not None
        assert cover.input_file_content[:2] == b"\xff\xd8"
        assert len(cover.input_file_content) <= 190_000
    finally:
        for p in created:
            if os.path.isfile(p):
                os.remove(p)


@pytest.mark.live
def test_live_youtube_mp3_download(proxy_env):
    import bot.handlers.youtube.mp3 as mp3mod
    from yt_dlp import YoutubeDL
    from pydub import AudioSegment

    with YoutubeDL(mp3mod.ydl_opts) as ydl:
        ydl.extract_info(VIDEO_URL, download=False)
        ydl.download([VIDEO_URL])
        filename = ydl.prepare_filename(
            {"title": "Me at the zoo", "ext": "mp3"}
        )

    candidates = {filename}
    # FFmpegExtractAudio may name the final file via the processed ext
    candidates |= set(
        glob.glob(DOWNLOAD_MP3_PATH + "Me*the*zoo*")
    )
    found = [p for p in candidates if os.path.isfile(p)]
    try:
        assert found, f"no mp3 found among {candidates}"
        mp3_path = found[0]
        audio = AudioSegment.from_file(mp3_path, format="mp3")
        assert len(audio) > 10_000  # >10 s of audio, in ms
    finally:
        for p in candidates:
            if os.path.isfile(p):
                os.remove(p)
