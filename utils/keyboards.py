from utils.constants.keyboards import *
from telegram import ReplyKeyboardMarkup

back_keyboard = [[BACK_KEYBOARD]]
back_keyboard_rm = ReplyKeyboardMarkup(back_keyboard, resize_keyboard=True)

start_keyboard = [[INSTAGRAM_KEYBOARD], [MUSIC_KEYBOARD]]
start_keyboard_rm = ReplyKeyboardMarkup(start_keyboard, resize_keyboard=True)

instagram_state_keyboard = [[INSTAGRAM_DOWNLOAD_KEYBOARD], [BACK_KEYBOARD, HOME_KEYBOARD]]
instagram_state_keyboard_rm = ReplyKeyboardMarkup(instagram_state_keyboard, resize_keyboard=True)

instagram_download_state_keyboard = [[BACK_KEYBOARD, HOME_KEYBOARD]]
instagram_download_state_keyboard_rm = ReplyKeyboardMarkup(instagram_download_state_keyboard, resize_keyboard=True)

music_state_keyboard = [[MUSIC_SPOTIFY_KEYBOARD], [BACK_KEYBOARD, HOME_KEYBOARD]]
music_state_keyboard_rm = ReplyKeyboardMarkup(music_state_keyboard, resize_keyboard=True)

music_spotify_state_keyboard = [[BACK_KEYBOARD, HOME_KEYBOARD]]
music_spotify_state_keyboard_rm = ReplyKeyboardMarkup(music_spotify_state_keyboard, resize_keyboard=True)