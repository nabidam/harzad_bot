"""Short-lived opaque token maps (UX redesign §3.2, §5.3, §11.2).

A token names one immutable action (a specific URL, a specific result
set, a specific track) instead of "the latest thing this user sent".
Tokens live in bot_data (per process, P0 — spec §2.5: in-memory is
allowed, persistence is P1) with a TTL; an expired or unknown token
resolves to None, and every caller answers a stale toast instead of
guessing.

Token length: 8 hex chars — the budget that keeps complete callback
payloads under the Bot API's 64-byte limit (utils/keyboards.py asserts
it).
"""
import time
import uuid
from typing import Any


class TokenStore:
    """token -> (value, expires_at) with lazy expiry."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float]] = {}

    def put(self, value: Any, ttl_seconds: float) -> str:
        token = uuid.uuid4().hex[:8]
        self._data[token] = (value, time.monotonic() + ttl_seconds)
        return token

    def get(self, token: str | None) -> Any | None:
        if not token:
            return None
        entry = self._data.get(token)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            self._data.pop(token, None)
            return None
        return value

    def drop(self, token: str | None) -> None:
        if token:
            self._data.pop(token, None)

    def __len__(self) -> int:
        return len(self._data)


def get_token_store(context, name: str) -> TokenStore:
    """Fetch (or lazily create) a named store in bot_data."""
    stores = context.application.bot_data.setdefault("token_stores", {})
    store = stores.get(name)
    if store is None:
        store = TokenStore()
        stores[name] = store
    return store
