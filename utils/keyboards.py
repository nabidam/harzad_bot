from utils.constants.keyboards import *
from telegram import ReplyKeyboardMarkup

back_keyboard = [[BACK_KEYBOARD]]
back_keyboard_rm = ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True)

start_keyboard = [[INSTAGRAM_KEYBOARD], [MUSIC_KEYBOARD], [PINTEREST_KEYBOARD], [YOUTUBE_KEYBOARD], [AI_KEYBOARD]]
start_keyboard_rm = ReplyKeyboardMarkup(start_keyboard, resize_keyboard=True)

instagram_state_keyboard = [[INSTAGRAM_DOWNLOAD_KEYBOARD], [BACK_KEYBOARD, HOME_KEYBOARD]]
instagram_state_keyboard_rm = ReplyKeyboardMarkup(instagram_state_keyboard, resize_keyboard=True)

instagram_download_state_keyboard = [[BACK_KEYBOARD, HOME_KEYBOARD]]
instagram_download_state_keyboard_rm = ReplyKeyboardMarkup(instagram_download_state_keyboard, resize_keyboard=True)

music_state_keyboard = [[MUSIC_SPOTIFY_KEYBOARD], [BACK_KEYBOARD, HOME_KEYBOARD]]
music_state_keyboard_rm = ReplyKeyboardMarkup(music_state_keyboard, resize_keyboard=True)

music_spotify_state_keyboard = [[BACK_KEYBOARD, HOME_KEYBOARD]]
music_spotify_state_keyboard_rm = ReplyKeyboardMarkup(music_spotify_state_keyboard, resize_keyboard=True)

pinterest_state_keyboard = [[BACK_KEYBOARD, HOME_KEYBOARD]]
pinterest_state_keyboard_rm = ReplyKeyboardMarkup(pinterest_state_keyboard, resize_keyboard=True)

youtube_state_keyboard = [[YOUTUBE_DOWNLOAD_KEYBOARD, YOUTUBE_MP3_KEYBOARD], [BACK_KEYBOARD, HOME_KEYBOARD]]
youtube_state_keyboard_rm = ReplyKeyboardMarkup(youtube_state_keyboard, resize_keyboard=True)

youtube_download_state_keyboard = [[BACK_KEYBOARD, HOME_KEYBOARD]]
youtube_download_state_keyboard_rm = ReplyKeyboardMarkup(youtube_download_state_keyboard, resize_keyboard=True)

ai_state_keyboard = [[AI_CHAT_KEYBOARD], [BACK_KEYBOARD, HOME_KEYBOARD]]
ai_state_keyboard_rm = ReplyKeyboardMarkup(ai_state_keyboard, resize_keyboard=True)

ai_chat_state_keyboard = [[BACK_KEYBOARD, HOME_KEYBOARD]]
ai_chat_state_keyboard_rm = ReplyKeyboardMarkup(ai_chat_state_keyboard, resize_keyboard=True)