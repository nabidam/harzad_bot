"""#14 utils.instagram / InstagramClient — exception mapping + client flow.

Live IG login is blocked on empty .env credentials (open issue) — these
tests cover everything up to the network boundary with a fake Client.
"""
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from instagrapi.exceptions import (
    BadCredentials,
    BadPassword,
    ChallengeError,
    ChallengeRequired,
    ClientError,
    ClientForbiddenError,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    PrivateError,
    RateLimitError,
)
from requests.exceptions import ProxyError

import utils.instagram as ig
from utils.InstagramClient import InstagramClient, handle_exception
from utils.exceptions.instagram import LoginException


def make_client(last_json=None):
    client = MagicMock()
    client.last_json = last_json if last_json is not None else {}
    return client


# --- handle_exception mapping --------------------------------------------------------

@pytest.mark.parametrize(
    "error",
    [
        BadPassword("bad"),
        BadCredentials("both username and password must be provided"),
        RateLimitError(),
        LoginRequired(),
        ChallengeRequired(),
        PleaseWaitFewMinutes(),
        ClientForbiddenError(),
        ClientError(),
        PrivateError(),
        ChallengeError(),
    ],
)
def test_mapped_exceptions_become_login_exception(monkeypatch, error):
    from utils import InstagramClient as ic

    monkeypatch.setattr(ic, "next_proxy", lambda: "")
    client = make_client({})
    with pytest.raises(LoginException):
        handle_exception(client, error)


def test_unmapped_error_reraised():
    client = make_client({})
    with pytest.raises(ValueError):
        handle_exception(client, ValueError("unmapped"))


def test_feedback_required_blocked_messages_mapped(monkeypatch):
    from utils import InstagramClient as ic

    monkeypatch.setattr(ic, "next_proxy", lambda: "")
    for message in (
        "This action was blocked. Please try again later",
        "We restrict certain activity to protect our community",
        "Your account has been temporarily blocked",
    ):
        client = make_client({"feedback_message": message})
        with pytest.raises(LoginException):
            handle_exception(client, FeedbackRequired())


def test_feedback_required_other_messages_reraised(monkeypatch):
    from utils import InstagramClient as ic

    monkeypatch.setattr(ic, "next_proxy", lambda: "")
    client = make_client({"feedback_message": "something else entirely"})
    with pytest.raises(FeedbackRequired):
        handle_exception(client, FeedbackRequired())


def test_last_json_hacked_lock(monkeypatch):
    from utils import InstagramClient as ic

    monkeypatch.setattr(ic, "next_proxy", lambda: "")
    client = make_client({"challenge_type_enum_str": "HACKED_LOCK"})
    with pytest.raises(LoginException):
        handle_exception(client, ChallengeError())


def test_last_json_scraping_warning(monkeypatch):
    from utils import InstagramClient as ic

    monkeypatch.setattr(ic, "next_proxy", lambda: "")
    client = make_client({"challenge_type_enum_str": "SCRAPING_WARNING"})
    with pytest.raises(LoginException):
        handle_exception(client, ChallengeError())


def test_last_json_logged_out(monkeypatch):
    from utils import InstagramClient as ic

    monkeypatch.setattr(ic, "next_proxy", lambda: "")
    client = make_client({"message": "user_has_logged_out"})
    with pytest.raises(LoginException):
        handle_exception(client, LoginRequired())


def test_last_json_challenge_path(monkeypatch):
    from utils import InstagramClient as ic

    monkeypatch.setattr(ic, "next_proxy", lambda: "")
    client = make_client({"challenge": {"api_path": "/challenge/"}})
    with pytest.raises(LoginException):
        handle_exception(client, ChallengeRequired())


def test_last_json_ip_block_swaps_proxy(monkeypatch):
    from utils import InstagramClient as ic

    monkeypatch.setattr(ic, "next_proxy", lambda: "http://p:9")
    client = make_client(
        {"message": "Instagram has blocked your IP address, "
                    "use a quality proxy provider (not free, not shared)"}
    )
    with pytest.raises(LoginException):
        handle_exception(client, RateLimitError())
    # next_proxy() is polled twice, the winner is applied once
    assert client.set_proxy.call_count == 1
    client.set_proxy.assert_called_with("http://p:9")


def test_proxy_error_mapped(monkeypatch):
    from utils import InstagramClient as ic

    monkeypatch.setattr(ic, "next_proxy", lambda: "http://p:9")
    client = make_client({})
    with pytest.raises(LoginException):
        handle_exception(client, ProxyError())
    assert client.set_proxy.call_count >= 1


# --- get_client flow (fake instagrapi Client) -------------------------------------------

@pytest.fixture
def fake_client_factory(monkeypatch):
    instances = []

    def factory(request_timeout=None):
        c = MagicMock()
        c.last_json = {}
        instances.append(c)
        return c

    from utils import InstagramClient as ic

    monkeypatch.setattr(ic, "Client", factory)
    return instances


def test_get_client_no_session_logs_in_and_dumps(fake_client_factory, tmp_path):
    ic_client = InstagramClient(username="u", password="p")
    result = ic_client.get_client(
        login_directory=str(tmp_path),
        telegram_user_id="123",
        save_session=True,
    )
    c = fake_client_factory[0]
    assert result is c
    # the current implementation logs in twice on the no-session path
    # (once to dump, once to validate) — pinned here on purpose
    assert c.login.call_count == 2
    c.dump_settings.assert_called_once()
    assert c.load_settings.call_count == 0
    c.get_timeline_feed.assert_not_called()


def test_get_client_existing_session_loads_and_validates(
    fake_client_factory, tmp_path
):
    session = tmp_path / "u_123.json"
    session.write_text("{}")
    ic_client = InstagramClient(username="u", password="p")
    result = ic_client.get_client(
        login_directory=str(tmp_path),
        telegram_user_id="123",
        user_instagram_session=str(session),
        save_session=True,
    )
    c = fake_client_factory[0]
    assert result is c
    c.load_settings.assert_called_once()
    c.login.assert_called_once()
    c.get_timeline_feed.assert_called_once()
    c.dump_settings.assert_not_called()


def test_get_client_verification_code_path(fake_client_factory, tmp_path):
    ic_client = InstagramClient(username="u", password="p", verification_code="123456")
    ic_client.get_client(
        login_directory=str(tmp_path),
        telegram_user_id="123",
        save_session=True,
    )
    c = fake_client_factory[0]
    for call in c.login.call_args_list:
        assert call.kwargs.get("verification_code") == "123456"


def test_get_client_login_failure_propagates_login_exception(monkeypatch, tmp_path):
    from utils import InstagramClient as ic

    fake = MagicMock()
    fake.last_json = {}

    def fail_login(*a, **k):
        # mirror instagrapi internals: route the error through the
        # client's handle_exception, then re-raise
        err = BadPassword("wrong")
        try:
            ic.handle_exception(fake, err)
        except LoginException:
            raise
        raise err

    fake.login.side_effect = fail_login
    monkeypatch.setattr(ic, "Client", lambda **k: fake)
    ic_client = InstagramClient(username="u", password="p")
    with pytest.raises(LoginException):
        ic_client.get_client(
            login_directory=str(tmp_path), telegram_user_id="123", save_session=True
        )


# --- get_user_client_instagram ---------------------------------------------------------

def test_get_user_client_returns_client(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(
        ig, "InstagramClient",
        lambda username, password: MagicMock(
            get_client=MagicMock(return_value=sentinel)
        ),
    )
    assert ig.get_user_client_instagram() is sentinel


def test_get_user_client_login_failure_without_session_returns_none(
    monkeypatch, tmp_path
):
    # First failure must NOT crash on os.remove of a missing session file
    # (first-ever IG use: no session exists yet).
    calls = {"n": 0}

    class FakeIGClient:
        def __init__(self, username, password):
            pass

        def get_client(self, **kw):
            calls["n"] += 1
            raise LoginException({"status": "fail", "message": "BadPassword"})

    monkeypatch.setattr(ig, "InstagramClient", FakeIGClient)
    monkeypatch.chdir(tmp_path)  # login/ dir would not exist either
    (tmp_path / "login").mkdir()
    result = ig.get_user_client_instagram()
    assert result is None
    assert calls["n"] == 2  # one retry, then give up


def test_get_user_client_removes_bad_session_then_retries(
    monkeypatch, tmp_path
):
    from configurations.settings import ADMIN_TELEGRAM_USER_ID, INSTAGRAM_USER

    login_dir = tmp_path / "login"
    login_dir.mkdir()
    session = login_dir / f"{INSTAGRAM_USER}_{ADMIN_TELEGRAM_USER_ID}.json"
    session.write_text("{}")

    calls = {"n": 0}
    sentinel = object()

    class FakeIGClient:
        def __init__(self, username, password):
            pass

        def get_client(self, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise LoginException({"status": "fail", "message": "stale"})
            return sentinel

    monkeypatch.setattr(ig, "InstagramClient", FakeIGClient)
    monkeypatch.chdir(tmp_path)
    result = ig.get_user_client_instagram()
    assert result is sentinel
    assert calls["n"] == 2
    assert not session.exists()  # bad session deleted
