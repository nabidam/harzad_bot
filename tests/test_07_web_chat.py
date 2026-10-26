"""#7 web.app.handle_chat — role normalization + validation."""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.handlers.ai.chat import SYSTEM_PROMPT
from tests.conftest import make_init_data, user_params


@pytest.fixture
async def client(web_app):
    app, fake_bot = web_app
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c, fake_bot, app
    await c.close()


async def post_chat(client, messages, init_data=None):
    c, _, _ = client
    payload = {"messages": messages}
    if init_data is not None:
        payload["initData"] = init_data
    return await c.post("/api/chat", json=payload)


async def test_role_normalization_table(client, fake_llm):
    r = await post_chat(client, [
        {"role": "user", "content": "hi"},
        {"role": "bot", "content": "hello"},
        {"role": "ai", "content": "hullo"},
        {"role": "assistant", "content": "hey"},
        {"role": "human", "content": "yo"},
        {"role": "system", "content": "sys"},
        {"role": "mystery", "content": "???"},
    ])
    assert r.status == 200
    body = await r.json()
    assert body["reply"] == "test reply"
    calls = fake_llm.last_messages
    assert calls[0] == ("system", SYSTEM_PROMPT)
    assert calls[1:] == [
        ("human", "hi"),
        ("assistant", "hello"),
        ("assistant", "hullo"),
        ("assistant", "hey"),
        ("human", "yo"),
        ("system", "sys"),
        ("human", "???"),  # unknown role -> human
    ]


async def test_empty_content_entries_filtered(client, fake_llm):
    r = await post_chat(client, [
        {"role": "user", "content": ""},
        {"role": "user"},  # no content key
        {"role": "user", "content": "kept"},
    ])
    assert r.status == 200
    assert fake_llm.last_messages[-1] == ("human", "kept")


async def test_non_dict_entries_filtered(client, fake_llm):
    r = await post_chat(client, [
        "just a string",
        42,
        {"role": "user", "content": "kept"},
    ])
    assert r.status == 200
    assert fake_llm.last_messages[-1] == ("human", "kept")


async def test_all_empty_is_400(client):
    r = await post_chat(client, [{"role": "user", "content": ""}])
    assert r.status == 400
    assert (await r.json())["error"] == "No message."


async def test_missing_messages_is_400(client):
    c, _, _ = client
    r = await c.post("/api/chat", json={})
    assert r.status == 400


async def test_history_truncated_to_last_10(client, fake_llm):
    messages = [{"role": "user", "content": f"m{i}"} for i in range(12)]
    r = await post_chat(client, messages)
    assert r.status == 200
    assert len(fake_llm.last_messages) == 1 + 10  # system + last 10
    assert fake_llm.last_messages[1] == ("human", "m2")  # m0/m1 dropped


async def test_forged_init_data_401(client):
    r = await post_chat(
        client,
        [{"role": "user", "content": "hi"}],
        init_data=make_init_data(user_params(), token="wrong:token"),
    )
    assert r.status == 401


async def test_anonymous_chat_allowed(client):
    r = await post_chat(client, [{"role": "user", "content": "hi"}])
    assert r.status == 200
    assert (await r.json())["reply"] == "test reply"
