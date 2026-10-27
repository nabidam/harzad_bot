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


def restricted(func):
    """This decorator allows you to restrict the access of a handler to
    only the user_ids specified in ADMINS."""

    @wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        user_id = update.effective_user.id
        # ADMINS entries are strings (split(',') of an env var); compare
        # as strings, or the check denies every user including admins.
        if str(user_id) not in {a.strip() for a in ADMINS}:
            print(f"Unauthorized access denied for {user_id}.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapped

def sync_user(func):
    """This decorator sync user details in db.

    Soft failure (plan 1.6): a DB outage must not break the user's
    download — log a warning, trip the circuit breaker after repeated
    failures, and continue the handler either way.
    """

    @wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        assert update.message is not None
        message = update.message.text
        assert update.effective_user is not None
        user_id = update.effective_user.id
        first_name = update.effective_user.first_name
        last_name = update.effective_user.last_name
        username = update.effective_user.username

        if _db_breaker.is_open:
            logger.debug("sync_user: DB circuit open; skipping user sync")
        else:
            try:
                for db in get_db():
                    db_user = userCruds.user_exists(db=db, tg_id=user_id)
                    if db_user:
                        # update
                        # TODO log updates
                        db_user = userCruds.update_user(db=db, tg_id=user_id, username=username, first_name=first_name, last_name=last_name)
                        logger.info(f"user with {db_user.tg_id:} updated.")
                    else:
                        db_user = userCruds.create_user(db=db, tg_id=user_id, username=username, first_name=first_name, last_name=last_name)
                        logger.info(f"user with {db_user.tg_id:} created.")
                _db_breaker.record_success()
            except Exception:
                logger.warning("sync_user: DB unavailable; continuing without user sync", exc_info=True)
                _db_breaker.record_failure()

        return await func(update, context, *args, **kwargs)

    return wrapped

def log_message(func):
    """This decorator log message details in db.

    Soft failure (plan 1.6): sharing the sync_user circuit breaker —
    while it is open, logging is skipped instead of paying a connect
    timeout per message.
    """

    @wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        assert update.message is not None
        message = update.message.text
        assert update.effective_user is not None
        user_id = update.effective_user.id

        if _db_breaker.is_open:
            logger.debug("log_message: DB circuit open; skipping message log")
        else:
            try:
                for db in get_db():
                    db_log = logCruds.create_log(db=db, tg_id=user_id, message=message)
                _db_breaker.record_success()
            except Exception:
                logger.warning("log_message: DB unavailable; continuing without message log", exc_info=True)
                _db_breaker.record_failure()

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