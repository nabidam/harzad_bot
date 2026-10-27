"""Inline keyboards (UX redesign).

Layout follows the spec: Home is action-first (one primary CTA, then the
platform inputs, then Tasks/Help); every input screen gets [🏠 Home]
[📥 Tasks]; the format picker and task rows carry token-based callbacks so
each button identifies its exact original action — never "the latest
thing this user sent".

Callback data must stay ≤64 bytes (Bot API hard limit): tokens are 8 hex
chars, `_button` asserts it so a regression fails at build time, not at
the first user tap.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from utils import copy
from utils.constants.keyboards import (
    BACK_KEYBOARD,
    HOME_KEYBOARD,
    COPY_LINK_BUTTON,
)

# Bot API 9.4 button styles: the primary action of a screen gets the blue
# style; navigation and escape hatches stay neutral.
ACTION_STYLE = "primary"


def _button(label: str, cb: str | None = None, url: str | None = None,
            style: str | None = None) -> InlineKeyboardButton:
    if cb is not None:
        length = len(cb.encode("utf-8"))
        assert length <= 64, f"callback_data exceeds 64 bytes ({length}): {cb!r}"
        return InlineKeyboardButton(label, callback_data=cb, style=style)
    return InlineKeyboardButton(label, url=url, style=style)


def _action(label: str, cb: str) -> InlineKeyboardButton:
    return _button(label, cb, style=ACTION_STYLE)


def _home_tasks_row() -> list[InlineKeyboardButton]:
    """Exit row for input screens (spec §2.2)."""
    return [_button(copy.BTN_HOME, "nav:home"),
            _button(copy.BTN_TASKS, "nav:tasks")]


def home_tasks_row_keyboard() -> InlineKeyboardMarkup:
    """Standalone [🏠 Home] [📥 Tasks] row (used for guidance replies)."""
    return InlineKeyboardMarkup([_home_tasks_row()])


# --- Home (spec §2.1, exact row layout) ------------------------------------

home_keyboard = InlineKeyboardMarkup([
    [_action(copy.BTN_SEARCH, "nav:search")],
    [_button(copy.BTN_INSTAGRAM, "nav:ig"), _button(copy.BTN_YOUTUBE, "nav:yt")],
    [_button(copy.BTN_PINTEREST, "nav:pin"), _button(copy.BTN_AI, "nav:ai")],
    [_button(copy.BTN_TASKS, "nav:tasks"), _button(copy.BTN_HELP, "nav:help")],
])

# --- Input screens (spec §2.2) ---------------------------------------------

search_input_keyboard = InlineKeyboardMarkup([_home_tasks_row()])
instagram_input_keyboard = InlineKeyboardMarkup([_home_tasks_row()])
youtube_input_keyboard = InlineKeyboardMarkup([_home_tasks_row()])
pinterest_input_keyboard = InlineKeyboardMarkup([_home_tasks_row()])
ai_chat_keyboard = InlineKeyboardMarkup([[_button(copy.BTN_HOME, "nav:home")]])

# --- Help (spec §13.1) -------------------------------------------------------

help_keyboard = InlineKeyboardMarkup([
    [_action(copy.BTN_SEARCH, "nav:search"), _button(copy.BTN_TASKS, "nav:tasks")],
    [_button(copy.BTN_HOME, "nav:home")],
])

# --- Task status (spec §4.2) --------------------------------------------------

def search_cancel_keyboard(token: str) -> InlineKeyboardMarkup:
    """[⏹ Cancel search] [📥 Tasks] on the live search status (spec
    §4.2: Cancel for every long operation, Tasks when useful)."""
    return InlineKeyboardMarkup([
        [_button(copy.BTN_SEARCH_CANCEL, f"search:cancel:{token}"),
         _button(copy.BTN_TASKS, "nav:tasks")],
    ])


def search_timeout_keyboard(token: str) -> InlineKeyboardMarkup:
    """[🔎 Search again] [🏠 Home] on the search timeout result (spec
    §5.4). The token names the timed-out query (an empty result set),
    so retry reuses the search:again path and its 300 s stale rule."""
    return InlineKeyboardMarkup([
        [_button(copy.BTN_SEARCH_AGAIN, f"search:again:{token}"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def task_cancel_keyboard(token: str) -> InlineKeyboardMarkup:
    """[⏹ Cancel] [📥 Tasks] on a running task status."""
    return InlineKeyboardMarkup([
        [_button(copy.BTN_TASK_CANCEL, f"task:cancel:{token}"),
         _button(copy.BTN_TASKS, "nav:tasks")],
    ])


def task_stop_keyboard(token: str) -> InlineKeyboardMarkup:
    """[⏹ Stop] [🏠 Home] on an AI chat status (spec §9)."""
    return InlineKeyboardMarkup([
        [_button(copy.BTN_AI_STOP, f"task:cancel:{token}"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def task_result_keyboard(token: str, *, retry_failed: bool = False) -> InlineKeyboardMarkup:
    """Keyboard for a finished/failed task summary (spec §4.2, §6.1)."""
    row: list[InlineKeyboardButton] = []
    if retry_failed:
        row.append(_action(copy.BTN_RETRY_FAILED, f"task:retry:{token}"))
    row.append(_button(copy.BTN_TASKS, "nav:tasks"))
    row.append(_button(copy.BTN_HOME, "nav:home"))
    return InlineKeyboardMarkup([row])


# --- YouTube format picker (spec §3.2, exact layout) --------------------------
# All choices neutral: no format is recommended (spec §15 default).

def yt_format_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button(copy.YT_FORMAT_720, f"yt:fmt:{token}:720"),
         _button(copy.YT_FORMAT_360, f"yt:fmt:{token}:360")],
        [_button(copy.YT_FORMAT_MP3, f"yt:fmt:{token}:mp3")],
        [_button(copy.BTN_YT_CANCEL, f"yt:cancel:{token}")],
    ])


# --- Track cards (spec §5.3, §5.6) --------------------------------------------
# Two token kinds share the payload shape:
#   results token + index  -> in-chat carousel (owner-authorized)
#   track token, no index  -> shared inline card (anyone may tap)

def track_keyboard(token: str, *, index: int | None = None,
                   spotify_url: str | None = None,
                   inline: bool = False) -> InlineKeyboardMarkup:
    """Buttons for one track card.

    `inline=True` labels the download button for group-shared inline cards
    (the result is sent to the chat the button lives in). The Spotify URL
    button is omitted when absent/invalid (no dead buttons, spec §10).
    """
    i = f":{index}" if index is not None else ""
    dl_label = copy.BTN_TRACK_DOWNLOAD_INLINE if inline else copy.BTN_TRACK_DOWNLOAD
    row1: list[InlineKeyboardButton] = [
        _action(dl_label, f"song:dl:{token}{i}"),
        _action(copy.BTN_TRACK_PREVIEW, f"song:prev:{token}{i}"),
    ]
    if spotify_url:
        row1.append(_button("🔗 Open on Spotify", url=spotify_url))
    rows: list[list[InlineKeyboardButton]] = [row1]
    if index is not None:
        rows.append([
            _button(copy.BTN_TRACK_PREVIOUS, f"song:prevcard:{token}:{index}"),
            _button(copy.BTN_TRACK_NEXT, f"song:next:{token}:{index}"),
        ])
    rows.append([_button(copy.BTN_SEARCH_AGAIN, f"search:again:{token}"),
                 _button(copy.BTN_HOME, "nav:home")])
    return InlineKeyboardMarkup(rows)


def large_file_keyboard(link: str) -> InlineKeyboardMarkup:
    """[📋 Copy download link] [🏠 Home] [📥 Tasks] for the >50 MB case."""
    from telegram import CopyTextButton

    return InlineKeyboardMarkup([
        [CopyTextButton(COPY_LINK_BUTTON, data=link)],
        [_button(copy.BTN_HOME, "nav:home"), _button(copy.BTN_TASKS, "nav:tasks")],
    ])


# ---------------------------------------------------------------------------
# Legacy aliases (rollout, spec §16)
# Pre-redesign messages already sent to users carry the old callback ids;
# the NAVIGATOR below maps them onto the new modes. The old keyboard
# objects themselves are retired from the visible UI — these aliases keep
# in-flight code paths importable until their handlers are reworked.
# ---------------------------------------------------------------------------

start_keyboard_rm = home_keyboard
instagram_state_keyboard_rm = instagram_input_keyboard
instagram_download_state_keyboard_rm = instagram_input_keyboard
music_state_keyboard_rm = search_input_keyboard
music_spotify_state_keyboard_rm = search_input_keyboard
pinterest_state_keyboard_rm = pinterest_input_keyboard
youtube_state_keyboard_rm = youtube_input_keyboard
youtube_download_state_keyboard_rm = youtube_input_keyboard
youtube_mp3_state_keyboard_rm = youtube_input_keyboard
ai_state_keyboard_rm = ai_chat_keyboard
ai_chat_state_keyboard_rm = ai_chat_keyboard
ai_tti_state_keyboard_rm = ai_chat_keyboard
