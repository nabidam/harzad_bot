import asyncio
from functools import wraps
from logging import getLogger

from configurations.settings import ADMINS
from connectors.db import get_db
import cruds.user as userCruds
import cruds.log as logCruds
from utils.circuit_breaker import CircuitBreaker

logger = getLogger(__name__)

# Soft-failure guard (plan 1.6): the user-facing feature (download, chat,
# navigation) must keep working while the auxiliary logging DB is down.
# Both decorators hit the same database, so they share one breaker — one
# failure stream opens the circuit for both, and while open the DB calls
# are skipped entirely (no connect-timeout per message against a dead DB).
_db_breaker = CircuitBreaker("postgres", failure_threshold=3, reset_timeout=60.0)

# In-flight background DB tasks. Kept in a set so the loop holds a
# reference (an unreferenced task can be GC'd mid-flight) and so tests
# and shutdown can join them (join_pending_db_work).
_pending_db_tasks: set[asyncio.Task] = set()


def restricted(func):
    """This decorator allows you to restrict the access of the handler to
    only the user_ids specified in ADMINS."""

    @wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        user = getattr(update, "effective_user", None)
        user_id = user.id if user else None
        # ADMINS entries are strings (split(',') of an env var); compare
        # as strings, or the check denies every user including admins.
        if user_id is None or str(user_id) not in {a.strip() for a in ADMINS}:
            logger.warning(f"Unauthorized access denied for {user_id}.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapped


def _spawn_db_task(fn, *args):
    """Schedule a blocking DB side effect OFF the event loop (spec §4.1:
    the acknowledgement must go out before non-critical DB sync/logging).

    The work runs on a worker thread — a slow or dead Postgres must not
    stall polling or delay the user's first response. No running loop
    (e.g. called from a plain thread in a unit test) runs it inline.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _guarded_db(fn, *args)
        return None
    task = loop.create_task(_background_db_task(fn, *args))
    _pending_db_tasks.add(task)
    task.add_done_callback(_pending_db_tasks.discard)
    return task


async def _background_db_task(fn, *args):
    try:
        await asyncio.to_thread(_guarded_db, fn, *args)
    except Exception:
        # _guarded_db already logs; this is a last net (e.g. the thread
        # pool itself failing) — never let a background task scream.
        logger.warning("background DB task failed", exc_info=True)


def _guarded_db(fn, *args):
    """Run a blocking DB core and report to the shared breaker."""
    try:
        fn(*args)
        _db_breaker.record_success()
    except Exception:
        logger.warning("DB side effect failed; continuing", exc_info=True)
        _db_breaker.record_failure()


async def join_pending_db_work() -> bool:
    """Await all in-flight background DB tasks.

    Used by tests (assert the rows/breaker state deterministically) and
    by shutdown hooks. Returns True when at least one task was waited on.
    """
    if not _pending_db_tasks:
        return False
    pending = list(_pending_db_tasks)
    await asyncio.gather(*pending, return_exceptions=True)
    return True


def _sync_user_to_db(user):
    """Blocking core of sync_user — runs on a worker thread only."""
    user_id = user.id
    username, first_name, last_name = user.username, user.first_name, user.last_name
    for db in get_db():
        db_user = userCruds.user_exists(db=db, tg_id=user_id)
        if db_user:
            db_user = userCruds.update_user(
                db=db, tg_id=user_id, username=username,
                first_name=first_name, last_name=last_name,
            )
            logger.info(f"user with {db_user.tg_id:} updated.")
        else:
            db_user = userCruds.create_user(
                db=db, tg_id=user_id, username=username,
                first_name=first_name, last_name=last_name,
            )
            logger.info(f"user with {db_user.tg_id:} created.")


def _log_message_to_db(tg_id, message):
    """Blocking core of log_message — runs on a worker thread only."""
    for db in get_db():
        logCruds.create_log(db=db, tg_id=tg_id, message=message)


def sync_user(func):
    """Sync user details in db — in the BACKGROUND.

    Spec §4.1: acknowledgement target is <1 s, so the handler's first
    response goes out before this DB work. The work runs on a worker
    thread; a DB outage must not break the user's download (plan 1.6) —
    failures are logged and trip the shared circuit breaker, never shown
    to the user.
    """

    @wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        user = getattr(update, "effective_user", None)
        if user is not None:
            if _db_breaker.is_open:
                logger.debug("sync_user: DB circuit open; skipping user sync")
            else:
                _spawn_db_task(_sync_user_to_db, user)

        return await func(update, context, *args, **kwargs)

    return wrapped


def log_message(func):
    """Log message details in db — in the BACKGROUND.

    Same contract as sync_user: off the critical path, soft-failing,
    sharing the same circuit breaker (while it is open, logging is
    skipped instead of paying a connect timeout per message).
    """

    @wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        user = getattr(update, "effective_user", None)
        msg = getattr(update, "message", None) or getattr(update, "effective_message", None)
        text = msg.text if msg else None
        if user is not None and text is not None:
            if _db_breaker.is_open:
                logger.debug("log_message: DB circuit open; skipping message log")
            else:
                _spawn_db_task(_log_message_to_db, user.id, text)

        return await func(update, context, *args, **kwargs)

    return wrapped


def send_action(action):
    """Sends `action` while processing func command."""

    def decorator(func):
        @wraps(func)
        async def command_func(update, context, *args, **kwargs):
            await context.bot.send_chat_action(
                chat_id=update.effective_message.chat_id, action=action
            )
            return await func(update, context, *args, **kwargs)

        return command_func

    return decorator
