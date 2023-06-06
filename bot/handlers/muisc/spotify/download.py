import asyncio
from concurrent.futures import ThreadPoolExecutor
from logging import getLogger

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from connectors import spotdl
from connectors.spotdl import create_spotdl_instance
from utils.constants.keyboards import BACK_KEYBOARD
from utils.constants.states import MUSIC_SPOTIFY_STATE, START_STATE
from configurations.settings import SPOTIFY_CLIENT_SECRET, SPOTIFY_CLIENT_ID

from utils.decorators import send_action
from utils.helpers import send_md_msg
from utils import keyboards
from utils.Spotidl import Spotidl

# Init logger
# logger = getLogger(__name__)

def download_from_spotify(url: str):
    spotdl = Spotidl()
    songs = spotdl.search([url])
    return spotdl.download(songs[0])

async def await_download_from_spotify(url):
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, download_from_spotify, url)
    return result

@send_action(ChatAction.TYPING)
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assert update.message is not None
    message = update.message.text
    print(message)
    if message == BACK_KEYBOARD:
        await update.message.reply_text("backed", reply_markup=keyboards.start_keyboard_rm)
        return START_STATE

    # download process wth spotdl
    # spotdl = Spotdl(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)

    assert message is not None
    song, path = await await_download_from_spotify(message)
    # songs = spotdl.search([message])

    # # try:
    # #     loop = asyncio.get_event_loop()
    # # except RuntimeError:
    # #     loop = asyncio.new_event_loop()
    # #     asyncio.set_event_loop(loop)
            
    # # task = loop.create_task(user_insert_events(target))
    # # if not loop.is_running():
    # #     loop.run_until_complete(task)
    # print(songs)
    # # results = spotdl.download_songs(songs)
    # song, path = spotdl.download(songs[0])

    assert path is not None
    # print("song: ", song)
    print("path: ", path.name)
    caption = path.parts[-1].split(".")[0]
    
    assert update.effective_chat is not None
    message = path
    mp3 = open(path.name, 'rb')
    caption = path.parts[-1].split(".")[0]
    await context.bot.send_audio(chat_id=update.effective_chat.id, audio=mp3, caption=caption, reply_markup=keyboards.music_spotify_state_keyboard_rm)
    await send_md_msg(context.bot, update.effective_chat.id, message, keyboards.music_spotify_state_keyboard_rm)
    return MUSIC_SPOTIFY_STATE
