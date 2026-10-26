"""#6 web.app job lifecycle — create/poll/file Range matrix.

The downloader is stubbed (conftest `web_app`); a real download is covered
by the live tests in tests/test_11_ydl_opts.py.
"""
import asyncio
import os
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

import web.app as webapp
from tests.conftest import make_init_data, user_params

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
async def client(web_app):
    app, fake_bot = web_app
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c, fake_bot, app
    await c.close()


async def create_job(client, url=URL, init_data=None, extra=None):
    payload = {"url": url, **(extra or {})}
    if init_data is not None:
        payload["initData"] = init_data
    return await client.post("/api/jobs", json=payload)


async def wait_for_state(client, job_id, states, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = await client.get(f"/api/jobs/{job_id}")
        assert r.status == 200
        body = await r.json()
        if body["state"] in states:
            return body
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached {states}")


# --- create -----------------------------------------------------------------

async def test_anonymous_job_completes(client):
    c, bot, app = client
    r = await create_job(c)
    assert r.status == 200
    job_id = (await r.json())["id"]
    body = await wait_for_state(c, job_id, {"done", "error"})
    assert body["state"] == "done", body
    assert body["title"] == "Test Video"
    assert body["file_name"] == "fake_video.mp4"
    assert body["file_url"] == f"https://app.test/api/jobs/{job_id}/file"
    assert body["error"] is None
    bot.send_video.assert_not_awaited()  # no chat delivery for anonymous


async def test_authenticated_job_delivers_to_chat(client):
    from types import SimpleNamespace

    c, bot, app = client
    # A real PTB message carries a str file_id; the AsyncMock's auto-attr
    # would not be JSON-serializable in the status response.
    bot.send_video.return_value = SimpleNamespace(
        video=SimpleNamespace(file_id="fid_test_123")
    )
    init = make_init_data(user_params(user_id=555000111))
    r = await create_job(c, init_data=init)
    assert r.status == 200
    job_id = (await r.json())["id"]
    body = await wait_for_state(c, job_id, {"done", "error"})
    assert body["state"] == "done", body
    bot.send_video.assert_awaited_once()
    kwargs = bot.send_video.await_args.kwargs
    assert kwargs["chat_id"] == 555000111
    assert kwargs["supports_streaming"] is True
    assert kwargs["protect_content"] is True
    assert kwargs["duration"] == 42
    assert "Test Video" in kwargs["caption"]
    assert body["file_id"] == "fid_test_123"


async def test_over_50mb_skips_chat_delivery(client):
    c, bot, app = client
    app["_test_file_size"] = 50 * 1024 * 1024 + 1
    init = make_init_data(user_params(user_id=555000111))
    r = await create_job(c, init_data=init)
    job_id = (await r.json())["id"]
    body = await wait_for_state(c, job_id, {"done", "error"})
    assert body["state"] == "done", body
    assert body["file_size_mb"] >= 50
    bot.send_video.assert_not_awaited()  # app-only delivery


async def test_invalid_url_400(client):
    c, _, _ = client
    r = await create_job(c, url="not a url")
    assert r.status == 400


async def test_forged_init_data_401(client):
    c, _, _ = client
    r = await create_job(c, init_data=make_init_data(user_params(), token="wrong:token"))
    assert r.status == 401


async def test_double_active_job_409(client, monkeypatch):
    c, _, _ = client
    real = webapp._ytdlp_run

    def slow(url):
        time.sleep(0.6)
        return real(url)

    monkeypatch.setattr(webapp, "_ytdlp_run", slow)
    r1 = await create_job(c)
    assert r1.status == 200
    await asyncio.sleep(0.1)  # let the first job start running
    r2 = await create_job(c)
    assert r2.status == 409
    body = await r2.json()
    assert "Already downloading" in body["error"]


async def test_joberror_surfaces_message(client, monkeypatch):
    c, _, _ = client

    def boom(url):
        raise webapp.JobError("Custom boom")

    monkeypatch.setattr(webapp, "_ytdlp_run", boom)
    r = await create_job(c)
    job_id = (await r.json())["id"]
    body = await wait_for_state(c, job_id, {"done", "error"})
    assert body["state"] == "error"
    assert body["error"] == "Custom boom"


async def test_unexpected_error_generic_message(client, monkeypatch):
    c, _, _ = client

    def boom(url):
        raise RuntimeError("traceback noise")

    monkeypatch.setattr(webapp, "_ytdlp_run", boom)
    r = await create_job(c)
    job_id = (await r.json())["id"]
    body = await wait_for_state(c, job_id, {"done", "error"})
    assert body["state"] == "error"
    assert body["error"] == "Download failed. Try again."


# --- status -------------------------------------------------------------------

async def test_status_unknown_job_404(client):
    c, _, _ = client
    r = await c.get("/api/jobs/doesnotexist")
    assert r.status == 404


async def test_status_shape(client):
    c, _, _ = client
    r = await create_job(c)
    job_id = (await r.json())["id"]
    await wait_for_state(c, job_id, {"done", "error"})
    r = await c.get(f"/api/jobs/{job_id}")
    body = await r.json()
    assert set(body) == {
        "state", "title", "file_name", "file_size_mb", "file_url", "file_id", "error"
    }


# --- file download (Range matrix) ----------------------------------------------

async def test_file_download_full_and_ranges(client):
    c, _, _ = client
    r = await create_job(c)
    job_id = (await r.json())["id"]
    await wait_for_state(c, job_id, {"done"})
    size = 1024
    path = f"/api/jobs/{job_id}/file"

    # Full download
    r = await c.get(path)
    assert r.status == 200
    data = await r.read()
    assert data == b"0" * size
    assert r.headers["Content-Length"] == str(size)
    assert r.headers["Accept-Ranges"] == "bytes"
    assert r.headers["Content-Disposition"] == 'attachment; filename="fake_video.mp4"'
    assert r.headers["Access-Control-Allow-Origin"] == "https://web.telegram.org"
    assert r.headers["Content-Type"] == "video/mp4"

    # Partial range
    r = await c.get(path, headers={"Range": "bytes=100-199"})
    assert r.status == 206
    data = await r.read()
    assert len(data) == 100
    assert r.headers["Content-Range"] == f"bytes 100-199/{size}"

    # Open-ended range
    r = await c.get(path, headers={"Range": "bytes=100-"})
    assert r.status == 206
    data = await r.read()
    assert len(data) == size - 100
    assert r.headers["Content-Range"] == f"bytes 100-{size - 1}/{size}"

    # Suffix range (last N bytes)
    r = await c.get(path, headers={"Range": "bytes=-100"})
    assert r.status == 206
    data = await r.read()
    assert len(data) == 100
    assert r.headers["Content-Range"] == f"bytes {size - 100}-{size - 1}/{size}"

    # Unsatisfiable range
    r = await c.get(path, headers={"Range": f"bytes={size + 1}-"})
    assert r.status == 416
    assert r.headers["Content-Range"] == f"bytes */{size}"


async def test_file_unknown_job_404(client):
    c, _, _ = client
    r = await c.get("/api/jobs/nope/file")
    assert r.status == 404


async def test_file_not_done_404(client):
    c, _, _ = client
    r = await c.get(f"/api/jobs/{'x' * 32}/file")
    assert r.status == 404


# --- pruning --------------------------------------------------------------------

def test_prune_jobs_drops_only_stale_finished():
    now = time.time()
    jobs = {
        "old-done": {"state": "done", "created": now - 2 * 86400},
        "fresh-done": {"state": "done", "created": now - 3600},
        "old-error": {"state": "error", "created": now - 2 * 86400},
        "active": {"state": "extracting", "created": now - 2 * 86400},
    }
    webapp._prune_jobs(jobs)
    assert set(jobs) == {"fresh-done", "active"}
