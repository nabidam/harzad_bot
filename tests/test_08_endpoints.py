"""#8 web.app endpoint matrix — anonymous vs authenticated on every route."""
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.conftest import make_init_data, user_params

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
async def client(web_app):
    app, fake_bot = web_app
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c, fake_bot, app
    await c.close()


VALID_INIT = make_init_data(user_params(user_id=424242424))


async def test_index_serves_spa(client):
    c, _, _ = client
    r = await c.get("/")
    assert r.status == 200
    text = await r.text()
    static = Path(__file__).resolve().parent.parent / "web" / "static" / "index.html"
    assert text == static.read_text(encoding="utf-8")


def test_mini_app_copy_matches_spec_deck():
    """Spec §12.3: the Mini App copy is the canonical deck text, aligned
    with the bot's status vocabulary and error tone (spec §12.2). The SPA
    is a static file, so the strings are pinned against it here."""
    static = Path(__file__).resolve().parent.parent / "web" / "static" / "index.html"
    html = static.read_text(encoding="utf-8")
    for s in (
        "Paste a media link to download it.",           # placeholder
        "YouTube &middot; Instagram &middot; Pinterest &middot; TikTok &middot; Spotify",
        ">Download<",                                    # submit
        "Checking link\u2026",                           # loading
        "Getting media details\u2026",                   # bot vocabulary
        "This download failed. Check the link or try again.",
        ">Try again</button>",                           # retry
        "This download link may have expired. Generate a new one to continue.",
        "Ask me a question. I answer each message independently for now,",
        "https://t.me/harzad_bot",                       # handoff back to the bot
    ):
        assert s in html, f"missing Mini App copy: {s!r}"


async def test_health(client):
    c, _, _ = client
    r = await c.get("/api/health")
    assert r.status == 200
    assert await r.json() == {"ok": True}


async def test_auth_anonymous(client):
    c, _, _ = client
    r = await c.post("/api/auth", json={})
    assert r.status == 200
    assert await r.json() == {"user": None, "anonymous": True}


async def test_auth_valid_returns_filtered_user(client):
    c, _, _ = client
    r = await c.post("/api/auth", json={"initData": VALID_INIT})
    assert r.status == 200
    body = await r.json()
    assert body == {"user": {
        "id": 424242424,
        "first_name": "Test",
        "last_name": "User",
        "username": "testuser",
    }}
    assert "auth_date" not in body["user"]  # not leaked


async def test_auth_forced_401(client):
    c, _, _ = client
    r = await c.post(
        "/api/auth",
        json={"initData": make_init_data(user_params(), token="wrong:token")},
    )
    assert r.status == 401


async def test_auth_accepts_query_string_initdata(client):
    c, _, _ = client
    # No JSON body at all: _auth falls back to the query string.
    r = await c.post("/api/auth", params={"initData": VALID_INIT})
    assert r.status == 200
    assert (await r.json())["user"]["id"] == 424242424


async def test_chat_forged_401(client):
    c, _, _ = client
    r = await c.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}],
              "initData": make_init_data(user_params(), token="wrong:token")},
    )
    assert r.status == 401


async def test_jobs_forged_401(client):
    c, _, _ = client
    r = await c.post(
        "/api/jobs",
        json={"url": URL, "initData": make_init_data(user_params(), token="wrong:token")},
    )
    assert r.status == 401


async def test_jobs_unknown_id_404_no_auth_required(client):
    c, _, _ = client
    r = await c.get("/api/jobs/00000000000000000000000000000000")
    assert r.status == 404


async def test_unknown_route_404(client):
    c, _, _ = client
    r = await c.get("/api/nope")
    assert r.status == 404
