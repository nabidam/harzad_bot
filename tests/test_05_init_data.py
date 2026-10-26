"""#5 web.app.verify_init_data — the Telegram WebApp initData auth matrix."""
import json
import time

import pytest

import web.app as webapp
from tests.conftest import make_init_data, user_params


def test_valid_blob_top_level_auth_date():
    user = webapp.verify_init_data(make_init_data(user_params()))
    assert user is not None
    assert user["id"] == 777000111
    assert user["username"] == "testuser"


def test_valid_blob_embedded_auth_date_fallback():
    # Older clients put auth_date inside the user object only.
    params = user_params()
    stamp = params.pop("auth_date")
    user = json.loads(params["user"])
    user["auth_date"] = int(stamp)
    params["user"] = json.dumps(user)
    assert webapp.verify_init_data(make_init_data(params)) is not None


def test_empty_string_is_none():
    assert webapp.verify_init_data("") is None


def test_missing_hash_rejected():
    params = user_params()
    from urllib.parse import urlencode

    assert webapp.verify_init_data(urlencode(params)) is None


def test_forged_hash_rejected():
    blob = make_init_data(user_params())
    # Flip one character of the hash.
    i = blob.index("hash=") + len("hash=")
    flipped = "0" if blob[i] != "0" else "1"
    bad = blob[:i] + flipped + blob[i + 1:]
    assert webapp.verify_init_data(bad) is None


def test_hashed_against_wrong_token_rejected():
    blob = make_init_data(user_params(), token="123:other-bot-token")
    assert webapp.verify_init_data(blob) is None


def test_tampered_param_rejected():
    # Hash valid for user id 777000111, but the blob claims a different id.
    blob = make_init_data(user_params())
    assert webapp.verify_init_data(blob.replace("777000111", "1", 1)) is None


def test_stale_top_level_auth_date_rejected():
    assert webapp.verify_init_data(make_init_data(user_params(age_days=2))) is None


def test_stale_embedded_auth_date_rejected():
    params = user_params(age_days=2)
    stamp = params.pop("auth_date")
    user = json.loads(params["user"])
    user["auth_date"] = int(stamp)
    params["user"] = json.dumps(user)
    assert webapp.verify_init_data(make_init_data(params)) is None


def test_invalid_user_json_rejected():
    params = user_params()
    params["user"] = "not-json{"
    assert webapp.verify_init_data(make_init_data(params)) is None


def test_undocumented_extra_param_still_verifies():
    # The scheme hashes every param except `hash` — an undocumented blob
    # param (Telegram shipped a `signature` param without announcing it)
    # must be covered by the same hash and accepted.
    params = user_params()
    params["signature"] = "ab12cd"
    user = webapp.verify_init_data(make_init_data(params))
    assert user is not None and user["id"] == 777000111


def test_freshness_window_just_inside_ttl():
    # ~21.6 h old: inside the 24 h TTL (a blob aged exactly 24 h would be
    # rejected the instant it is checked, so don't sit on the boundary).
    assert webapp.verify_init_data(make_init_data(user_params(age_days=0.9))) is not None
