"""#3 utils.keyboards + constants — button/callback consistency.

The core invariant of Phase A: every callback_data that exists anywhere in
the keyboards must be a key of NAVIGATOR (the single dispatcher), and every
NAVIGATOR destination must be reachable from the start menu.
"""
from telegram import InlineKeyboardMarkup

import utils.keyboards as kb
from bot.navigation import NAVIGATOR
from utils.constants import keyboards as CK
from utils.constants.states import (
    AI_CHAT_STATE, AI_STATE, AI_TTI_STATE, INSTAGRAM_DOWNLOAD_STATE,
    INSTAGRAM_STATE, MUSIC_SPOTIFY_STATE, MUSIC_STATE, PINTEREST_STATE,
    START_STATE, YOUTUBE_DOWNLOAD_STATE, YOUTUBE_MP3_STATE, YOUTUBE_STATE,
)

ALL_NAV_CBS = {
    CK.CB_START, CK.CB_INSTAGRAM, CK.CB_INSTAGRAM_DOWNLOAD, CK.CB_MUSIC,
    CK.CB_MUSIC_SPOTIFY, CK.CB_PINTEREST, CK.CB_YOUTUBE,
    CK.CB_YOUTUBE_DOWNLOAD, CK.CB_YOUTUBE_MP3, CK.CB_AI, CK.CB_AI_CHAT,
}

ALL_KEYBOARDS = {
    name: value for name, value in vars(kb).items()
    if isinstance(value, InlineKeyboardMarkup)
}


def test_navigator_has_exactly_the_wired_states():
    assert set(NAVIGATOR) == ALL_NAV_CBS
    assert len(NAVIGATOR) == 11


def test_tti_not_wired():
    # TTI disabled 2026-10-26: constant kept (dormant tti.py imports it),
    # but it must not be a navigation destination.
    assert CK.CB_AI_TTI not in NAVIGATOR
    assert all(
        entry[2] != AI_TTI_STATE for entry in NAVIGATOR.values()
    )


def test_every_keyboard_callback_is_navigable():
    for name, markup in ALL_KEYBOARDS.items():
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.callback_data is not None:
                    assert btn.callback_data in NAVIGATOR, (
                        f"{name} button {btn.text!r} -> {btn.callback_data!r} "
                        f"is not a NAVIGATOR key"
                    )


def test_every_navigator_destination_reachable_from_start():
    # Graph: start keyboard -> each destination's keyboard -> ...
    graph: dict[str, set[str]] = {}

    def collect(markup: InlineKeyboardMarkup):
        cbs = set()
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    cbs.add(btn.callback_data)
        return cbs

    start_cbs = collect(kb.start_keyboard_rm)
    seen: set[str] = set()
    frontier = list(start_cbs)
    while frontier:
        cb = frontier.pop()
        if cb in seen:
            continue
        seen.add(cb)
        entry = NAVIGATOR.get(cb)
        if entry:
            frontier.extend(collect(entry[1]))
    assert seen == ALL_NAV_CBS, f"unreachable destinations: {ALL_NAV_CBS - seen}"


def test_navigator_entries_shape():
    for cb, (text, keyboard, state) in NAVIGATOR.items():
        assert isinstance(text, str) and text.strip(), cb
        assert isinstance(keyboard, InlineKeyboardMarkup), cb
        assert isinstance(state, int), cb


def test_states_are_sequential_ints():
    states = [
        START_STATE, INSTAGRAM_STATE, INSTAGRAM_DOWNLOAD_STATE,
        MUSIC_STATE, MUSIC_SPOTIFY_STATE, PINTEREST_STATE,
        YOUTUBE_STATE, YOUTUBE_DOWNLOAD_STATE, YOUTUBE_MP3_STATE,
        AI_STATE, AI_CHAT_STATE, AI_TTI_STATE,
    ]
    assert states == list(range(12))


# --- per-keyboard shape ----------------------------------------------------

def test_start_keyboard_five_sections():
    rows = kb.start_keyboard_rm.inline_keyboard
    assert len(rows) == 5
    labels = [row[0].text for row in rows]
    assert labels == [
        CK.INSTAGRAM_KEYBOARD, CK.MUSIC_KEYBOARD, CK.PINTEREST_KEYBOARD,
        CK.YOUTUBE_KEYBOARD, CK.AI_KEYBOARD,
    ]
    for row in rows:
        assert len(row) == 1
        assert row[0].style == kb.ACTION_STYLE  # action buttons are primary


def test_back_home_row_present_on_every_menu_screen():
    # Every screen that follows a prompt ends with a Back + Home row.
    cases = {
        kb.instagram_state_keyboard_rm: (CK.CB_START, {CK.CB_INSTAGRAM_DOWNLOAD}),
        kb.music_state_keyboard_rm: (CK.CB_START, {CK.CB_MUSIC_SPOTIFY}),
        kb.pinterest_state_keyboard_rm: (CK.CB_START, set()),
        kb.youtube_state_keyboard_rm: (
            CK.CB_START, {CK.CB_YOUTUBE_DOWNLOAD, CK.CB_YOUTUBE_MP3}
        ),
        kb.ai_state_keyboard_rm: (CK.CB_START, {CK.CB_AI_CHAT}),
        kb.instagram_download_state_keyboard_rm: (CK.CB_INSTAGRAM, set()),
        kb.music_spotify_state_keyboard_rm: (CK.CB_MUSIC, set()),
        kb.youtube_download_state_keyboard_rm: (CK.CB_YOUTUBE, set()),
        kb.youtube_mp3_state_keyboard_rm: (CK.CB_YOUTUBE, set()),
        kb.ai_chat_state_keyboard_rm: (CK.CB_AI, set()),
    }
    for keyboard, (back_cb, action_cbs) in cases.items():
        rows = keyboard.inline_keyboard
        back_row = rows[-1]
        assert [b.text for b in back_row] == [CK.BACK_KEYBOARD, CK.HOME_KEYBOARD]
        assert back_row[0].callback_data == back_cb
        assert back_row[1].callback_data == CK.CB_START
        top_cbs = {b.callback_data for row in rows[:-1] for b in row}
        assert top_cbs == action_cbs, keyboard
