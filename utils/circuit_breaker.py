"""Tiny circuit breaker for non-critical side effects.

The bot must keep serving users even when an auxiliary subsystem (the
Postgres logging DB, an external API) is down. Callers wrap the side
effect in try/except and report back with record_success /
record_failure. After `failure_threshold` consecutive failures the
circuit opens for `reset_timeout` seconds; while open, `is_open` is
True and callers skip the side effect entirely — otherwise every
message would pay the full connect timeout against a dead DB.

Single-loop usage (asyncio): no locking needed.
"""
import time
from logging import getLogger

logger = getLogger(__name__)


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 3, reset_timeout: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        """True while tripped and still inside the cool-off window.

        After `reset_timeout` the breaker is half-open: it reports
        closed so the next call can probe the dependency. The failure
        counter is left at the threshold, so a failed probe re-opens the
        circuit immediately, while a success closes it for good.
        """
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.reset_timeout:
            return False
        return True

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._opened_at = time.monotonic()
            logger.warning(
                "circuit %r opened for %.0fs after %d consecutive failures",
                self.name, self.reset_timeout, self._consecutive_failures,
            )

    def reset(self) -> None:
        """Force the closed state (used by tests)."""
        self._consecutive_failures = 0
        self._opened_at = None
