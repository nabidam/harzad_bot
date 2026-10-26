from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from utils.constants.keyboards import *

# Bot API 9.4 (2026-02-09) button styles. Clients released before that date
# ignore the field and render the default style, so this is safe to ship.
#
# Design rule: action buttons (the thing the user actually does) get the
# `primary` blue style; Back/Home navigation stays neutral so the eye goes
# to the action first.
ACTION_STYLE = "primary"


def _action(label: str, cb: str) -> InlineKeyboardButton:
    """A blue action button — the primary thing to do on the screen."""
    return InlineKeyboardButton(label, callback_data=cb, style=ACTION_STYLE)


def _back_home(back_cb: str):
    """Back/Home row for the keyboard that follows a prompt screen."""
    return [
        InlineKeyboardButton(BACK_KEYBOARD, callback_data=back_cb),
        InlineKeyboardButton(HOME_KEYBOARD, callback_data=CB_START),
    ]


start_keyboard_rm = InlineKeyboardMarkup([
    [_action(INSTAGRAM_KEYBOARD, CB_INSTAGRAM)],
    [_action(MUSIC_KEYBOARD, CB_MUSIC)],
    [_action(PINTEREST_KEYBOARD, CB_PINTEREST)],
    [_action(YOUTUBE_KEYBOARD, CB_YOUTUBE)],
    [_action(AI_KEYBOARD, CB_AI)],
])

instagram_state_keyboard_rm = InlineKeyboardMarkup([
    [_action(INSTAGRAM_DOWNLOAD_KEYBOARD, CB_INSTAGRAM_DOWNLOAD)],
    _back_home(CB_START),
])

instagram_download_state_keyboard_rm = InlineKeyboardMarkup([
    _back_home(CB_INSTAGRAM),
])

music_state_keyboard_rm = InlineKeyboardMarkup([
    [_action(MUSIC_SPOTIFY_KEYBOARD, CB_MUSIC_SPOTIFY)],
    _back_home(CB_START),
])

music_spotify_state_keyboard_rm = InlineKeyboardMarkup([
    _back_home(CB_MUSIC),
])

pinterest_state_keyboard_rm = InlineKeyboardMarkup([
    _back_home(CB_START),
])

youtube_state_keyboard_rm = InlineKeyboardMarkup([
    [
        _action(YOUTUBE_DOWNLOAD_KEYBOARD, CB_YOUTUBE_DOWNLOAD),
        _action(YOUTUBE_MP3_KEYBOARD, CB_YOUTUBE_MP3),
    ],
    _back_home(CB_START),
])

youtube_download_state_keyboard_rm = InlineKeyboardMarkup([
    _back_home(CB_YOUTUBE),
])

youtube_mp3_state_keyboard_rm = InlineKeyboardMarkup([
    _back_home(CB_YOUTUBE),
])

ai_state_keyboard_rm = InlineKeyboardMarkup([
    [_action(AI_CHAT_KEYBOARD, CB_AI_CHAT)],
    _back_home(CB_START),
])

ai_chat_state_keyboard_rm = InlineKeyboardMarkup([
    _back_home(CB_AI),
])

ai_tti_state_keyboard_rm = InlineKeyboardMarkup([
    _back_home(CB_AI),
])
