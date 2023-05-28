from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, filters
from bot.handlers import start, first, second, final
from utils import shared_handlers
from utils.constants.keyboards import *

from utils.constants.states import *

def main_conversation_handler():
    """Process a /start command."""
    conversation_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start.handler)],
        states={
            START_STATE: [
                MessageHandler(filters.Regex(f"^{FIRST_KEYBOARD}$"), first.handler),
                *shared_handlers.shared_handlers
            ],
            FIRST_STATE: [
                MessageHandler(filters.Regex(f"^{SECOND_KEYBOARD}$"), second.handler),
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), start.handler),
                *shared_handlers.shared_handlers
            ],
            SECOND_STATE: [
                MessageHandler(filters.Regex(f"^{FINAL_KEYBOARD}$"), final.handler),
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), first.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                *shared_handlers.shared_handlers
            ],
            FINAL_STATE: [
                MessageHandler(filters.Regex(f"^{BACK_KEYBOARD}$"), second.handler),
                MessageHandler(filters.Regex(f"^{HOME_KEYBOARD}$"), start.handler),
                *shared_handlers.shared_handlers
            ]
        },
        fallbacks=[],
        name="main_handler",
        persistent=True,
    )
    return conversation_handler