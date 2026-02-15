from telegram.ext import CallbackContext, CommandHandler, ExtBot
from spotdl import Spotdl

# class MyContext(CallbackContext):
#     def __init__(self, application, chat_id=None, user_id=None):
#         super().__init__(application, chat_id, user_id)
#         self.spotdl: Spotdl = application.bot_data["spotdl_instance"]
        
class MyContext(CallbackContext[ExtBot, dict, dict, dict]):
    @property
    def spotdl(self) -> Spotdl:
        return self.application.bot_data["spotdl_instance"]