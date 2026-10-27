"""#15 live feature smoke — real external services (run with `pytest -m live`).

These are the KB §11 "live part" entries that are not covered elsewhere:
Spotify (spotdl with the real client credentials from .env) and Pinterest
(yt-dlp against a real pin). AI chat is verified via the production Mini App;
YouTube video/mp3 in test_11.
"""
import os

import pytest


@pytest.fixture
def proxy_env(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10808")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10808")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")


@pytest.mark.live
def test_live_pinterest_download(proxy_env, monkeypatch):
    """Real pin via yt-dlp, exactly the pipeline the handler uses.

    Needs a real, live pin URL (Pinterest serves no server-rendered pin
    ids to plain scrapers). Provide one via PINTEREST_TEST_PIN; the test
    skips itself otherwise.
    """
    import os

    from mutagen import File as MutagenFile
    from utils.helpers import prepare_cover
    from utils.youtube_dl import YDL_OPTS
    from yt_dlp import YoutubeDL

    pin = os.environ.get("PINTEREST_TEST_PIN")
    if not pin:
        pytest.skip("PINTEREST_TEST_PIN not set (needs a real live pin URL)")

    with YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(pin, download=False)
        assert info.get("title")
        ydl.download([pin])
        filename = ydl.prepare_filename(info)

    created = [filename]
    try:
        assert os.path.isfile(filename), filename
        assert os.path.getsize(filename) > 5_000
        assert int(MutagenFile(filename).info.length) > 0
        base = os.path.splitext(filename)[0]
        for ext in (".jpg", ".jpeg", ".webp", ".png"):
            if os.path.isfile(base + ext):
                created.append(base + ext)
        if create := prepare_cover(filename):
            assert create.input_file_content[:2] == b"\xff\xd8"
    finally:
        for p in created:
            if os.path.isfile(p):
                os.remove(p)


@pytest.mark.live
async def test_live_spotify_search_and_download(proxy_env, tmp_path):
    """Real Spotify search + download through the PRODUCTION code path.

    The download goes via `await_download_from_spotify` — the same
    run_in_executor wrapper the bot uses — because spotdl 4.6 needs a
    current event loop in the worker thread (see the 2026-10-27 crash);
    calling spotdl.download() from the main thread would pass here and
    still crash in the bot.
    """
    from bot.handlers.music.spotify.download import await_download_from_spotify
    from configurations.settings import (
        SPOTIFY_CLIENT_ID,
        SPOTIFY_CLIENT_SECRET,
        YT_COOKIE_FILE,
        YT_PROXY,
    )
    from spotdl import Spotdl

    assert SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET

    spotdl = Spotdl(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        downloader_settings={
            "simple_tui": True,
            "yt_dlp_args": (
                f"--cookies {YT_COOKIE_FILE} --ignore-errors --impersonate chrome"
                f" --force-ipv4 --proxy {YT_PROXY}"
            ),
            "output": str(tmp_path),
            "parallel_downloads": 1,
        },
    )

    # a very short public track keeps the live test fast
    songs = spotdl.search(["concerto for rain"])
    assert songs, "spotify search returned nothing"
    song, path = await await_download_from_spotify(spotdl, songs[0])
    assert path is not None, "download produced no file"
    assert song is not None
    assert song.json.get("name")
    mp3 = tmp_path / path.name
    assert mp3.is_file()
    assert mp3.stat().st_size > 5_000
    assert song.cover_url  # the handler asserts this before sending
