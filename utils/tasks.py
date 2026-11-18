"""In-memory task registry (UX redesign §2.5, §8).

A task is NOT a screen: tasks keep running when the user navigates away;
only an explicit cancel stops one. The registry tracks every running
operation (search, download, AI answer) with an opaque token so status
buttons, cancels and retries address the exact task — never "the latest
thing this user sent".

P0 is in-memory by design (spec §2.5: "the task registry may be in-memory
as allowed by the current feasibility guide"); persistence is P1. Every
callback must fail gracefully when a token is unknown (process restart):
the handlers answer a stale toast instead of guessing.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# --- statuses -----------------------------------------------------------------
RUNNING: str = "running"
CANCEL_REQUESTED: str = "cancel_requested"
DONE: str = "done"
FAILED: str = "failed"
CANCELLED: str = "cancelled"
TIMED_OUT: str = "timed_out"

ACTIVE_STATUSES = frozenset({RUNNING, CANCEL_REQUESTED})
TERMINAL_STATUSES = frozenset({DONE, FAILED, CANCELLED, TIMED_OUT})

# kind -> short verb for the Tasks view rows (spec §8.2)
TASK_LABELS = {
    "search": "Searching",
    "spotify": "Downloading",
    "youtube_video": "Downloading",
    "youtube_mp3": "Downloading",
    "instagram": "Downloading",
    "pinterest": "Downloading",
    "tiktok": "Downloading",
    "ai": "Thinking",
}

# How many finished tasks the Tasks view keeps for "recent" (spec §8.3:
# max 5 shown; keep a bit more in memory so the page stays stable).
RECENT_KEEP = 20
RECENT_SHOW = 5
ACTIVE_PAGE_SIZE = 5
MAX_FINISHED_TASKS_KEEP = 200
MAX_FINISHED_TASK_AGE_SECONDS = 3600.0


def make_token() -> str:
    """8-hex-char token: fits the ≤64-byte callback budget next to the
    action prefix (e.g. `task:cancel:` + 8 = 19 bytes)."""
    return uuid.uuid4().hex[:8]


@dataclass
class Task:
    token: str
    owner_user_id: int
    chat_id: int
    kind: str
    input: str  # original input (URL or query) — shown truncated in Tasks
    status: str = RUNNING
    phase: str = "accepted"  # accepted | resolving | downloading | uploading | ...
    title: str | None = None
    item_total: int | None = None
    item_done: int = 0
    item_failed: int = 0
    status_message_id: int | None = None
    cancellable: bool = True
    # Safe-to-replay payload for Retry (spec §7.3); None = no retry button.
    retry_payload: Any = None
    # Worker-facing cancel signal; the status engine renders the honest
    # "requested vs confirmed" distinction (spec §4.3).
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    note: str | None = None  # human summary for the recent list, e.g. "sent"

    def elapsed(self) -> int:
        end = self.finished_at or time.monotonic()
        return max(0, int(end - self.created_at))

    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def touch(self, **fields) -> None:
        for key, value in fields.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.updated_at = time.monotonic()

    def row(self) -> str:
        """One Tasks-view row (spec §8.2): verb, subject, phase, elapsed,
        item progress when known."""
        label = TASK_LABELS.get(self.kind, self.kind)
        subject = self.title or (self.input[:40] + ("…" if len(self.input) > 40 else ""))
        line2 = f"{self.phase} · ⏱ {self.elapsed()}s elapsed"
        if self.item_total:
            line2 += f" · item {self.item_done}/{self.item_total}"
        if self.status == CANCEL_REQUESTED:
            line2 = "cancellation requested · " + line2
        return f"<b>{label}:</b> {subject}\n{line2}"

    def recent_row(self) -> str:
        """One recent (finished) task row (spec §8.3)."""
        icon = {"done": "✅", "failed": "⚠️", "cancelled": "⏹️", "timed_out": "⚠️"}.get(self.status, "•")
        subject = self.title or (self.input[:30] + ("…" if len(self.input) > 30 else ""))
        note = f" — {self.note}" if self.note else ""
        return f"{icon} {subject}{note}"


class TaskRegistry:
    """All tasks of this process, keyed by token."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._recent: list[Task] = []

    def create(
        self,
        *,
        owner_user_id: int,
        chat_id: int,
        kind: str,
        input: str,
        cancellable: bool = True,
        title: str | None = None,
        retry_payload: Any | None = None,
    ) -> Task:
        task = Task(
            token=make_token(),
            owner_user_id=owner_user_id,
            chat_id=chat_id,
            kind=kind,
            input=input,
            status=RUNNING,
            cancellable=cancellable,
            title=title,
            retry_payload=retry_payload,
        )
        self._tasks[task.token] = task
        return task

    def get(self, token: str | None) -> Task | None:
        if not token:
            return None
        return self._tasks.get(token)

    def active_for(self, owner_user_id: int) -> list[Task]:
        """This user's active tasks, most recently updated first."""
        tasks = [
            t for t in self._tasks.values()
            if t.owner_user_id == owner_user_id and t.is_active()
        ]
        tasks.sort(key=lambda t: t.updated_at, reverse=True)
        return tasks

    def all_active(self) -> list[Task]:
        tasks = [t for t in self._tasks.values() if t.is_active()]
        tasks.sort(key=lambda t: t.updated_at, reverse=True)
        return tasks

    def finish(self, token: str, status: str, *, note: str | None = None) -> Task | None:
        """Mark a task terminal and move it to the recent list."""
        task = self.get(token)
        if task is None:
            return None
        task.status = status
        task.finished_at = time.monotonic()
        if note is not None:
            task.note = note
        self._recent.insert(0, task)
        del self._recent[RECENT_KEEP:]
        self.prune()
        return task

    def prune(self) -> int:
        """Prune old terminal tasks from memory to prevent unbounded growth."""
        now = time.monotonic()
        recent_tokens = {t.token for t in self._recent}
        stale = [
            token for token, t in self._tasks.items()
            if not t.is_active()
            and token not in recent_tokens
            and (
                (now - (t.finished_at or t.updated_at) > MAX_FINISHED_TASK_AGE_SECONDS)
                or len(self._tasks) > MAX_FINISHED_TASKS_KEEP
            )
        ]
        for token in stale:
            self._tasks.pop(token, None)
        return len(stale)

    def recent_for(self, owner_user_id: int, limit: int = RECENT_SHOW) -> list[Task]:
        return [t for t in self._recent if t.owner_user_id == owner_user_id][:limit]

    def __len__(self) -> int:
        return len(self._tasks)


def get_registry(context) -> TaskRegistry:
    """Fetch (or lazily create) the process-wide registry in bot_data."""
    registry = context.application.bot_data.get("task_registry")
    if registry is None:
        registry = TaskRegistry()
        context.application.bot_data["task_registry"] = registry
    return registry
