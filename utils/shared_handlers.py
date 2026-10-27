from telegram.ext import CommandHandler

from bot.handlers import start, help, tasks

# /start and /help are accepted in every input mode (spec §2.3), as are
# the new /tasks and /cancel.
shared_handlers = [
    CommandHandler("start", start.handler),
    CommandHandler("help", help.handler),
    CommandHandler("tasks", tasks.on_tasks_command),
    CommandHandler("cancel", tasks.on_cancel_command),
]
