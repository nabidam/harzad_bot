from functools import wraps

from configurations.settings import ADMINS
from connectors.db import get_db
import cruds.user as userCruds


def restricted(func):
    """This decorator allows you to restrict the access of a handler to
    only the user_ids specified in ADMINS."""

    @wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMINS:
            print(f"Unauthorized access denied for {user_id}.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapped

def sync_user(func):
    """This decorator sync user details in db."""

    @wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        assert update.message is not None
        message = update.message.text
        assert update.effective_user is not None
        user_id = update.effective_user.id
        first_name = update.effective_user.first_name
        last_name = update.effective_user.last_name
        username = update.effective_user.username
        
        for db in get_db():
            db_user = userCruds.user_exists(db=db, tg_id=user_id)
            if db_user:
                # update
                # TODO log updates
                db_user = userCruds.update_user(db=db, tg_id=user_id, username=username, first_name=first_name, last_name=last_name)
                print(f"user with {db_user.tg_id:} updated.")
            else:
                db_user = userCruds.create_user(db=db, tg_id=user_id, username=username, first_name=first_name, last_name=last_name)
                print(f"user with {db_user.tg_id:} created.")
        
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