"""#3 utils.keyboards + constants — button/callback consistency (redesign).

Invariants:
- the Home keyboard matches spec §2.1 + the music-library plan §5
  (5 rows, one primary CTA, 📚 My music next to Tasks);
- every input screen ends with [🏠 Home] [📥 Tasks] (spec §2.2);
- every callback_data in any keyboard is either a NAVIGATOR key or a
  known tokenized-action pattern (yt/song/task/search/library);
- every NAVIGATOR destination is reachable from Home;
- no callback payload exceeds the Bot API 64-byte limit;
- TTI has no visible entry point (spec §10).
"""
import re

from telegram import InlineKeyboardMarkup

import utils.keyboards as kb
from bot.navigation import NAVIGATOR
from utils import copy
from utils.constants import keyboards as CK
from utils.constants.states import (
    AI_CHAT_STATE,
    AI_STATE,
    HOME_STATE,
    INSTAGRAM_INPUT_STATE,
    PINTEREST_INPUT_STATE,
    SEARCH_INPUT_STATE,
    YOUTUBE_INPUT_STATE,
)

ALL_NAV_CBS = {
    CK.CB_START, CK.CB_HOME, CK.CB_SEARCH, CK.CB_INSTAGRAM,
    CK.CB_INSTAGRAM_DOWNLOAD, CK.CB_MUSIC, CK.CB_MUSIC_SPOTIFY,
    CK.CB_PINTEREST, CK.CB_YOUTUBE, CK.CB_YOUTUBE_DOWNLOAD,
    CK.CB_YOUTUBE_MP3, CK.CB_AI, CK.CB_AI_CHAT,
}

# Tokenized action families handled outside NAVIGATOR (spec §16).
ACTION_PATTERNS = [
    re.compile(CK.YT_FMT_CALLBACK_PATTERN),
    re.compile(CK.YT_CANCEL_CALLBACK_PATTERN),
    re.compile(CK.YT_FMT_LEGACY_PATTERN),
    re.compile(CK.SONG_ACTION_CALLBACK_PATTERN),
    re.compile(CK.SONG_ACTION_LEGACY_PATTERN),
    re.compile(CK.SEARCH_CANCEL_CALLBACK_PATTERN),
    re.compile(CK.SEARCH_AGAIN_CALLBACK_PATTERN),
    re.compile(CK.LIB_ACTION_CALLBACK_PATTERN),
    re.compile(CK.LIB_PAGE_CALLBACK_PATTERN),
    re.compile(CK.TASK_CANCEL_CALLBACK_PATTERN),
    re.compile(CK.TASK_RETRY_CALLBACK_PATTERN),
    re.compile(CK.TASK_PAGE_CALLBACK_PATTERN),
]


def _callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data
    ]


def test_navigator_has_new_and_legacy_callbacks():
    assert ALL_NAV_CBS <= set(NAVIGATOR)
    # Tasks/Help are views, not NAVIGATOR states
    assert CK.CB_TASKS not in NAVIGATOR
    assert CK.CB_HELP not in NAVIGATOR


def test_tti_not_wired():
    # The dead feature has no visible entry point (spec §10): the constant
    # stays for the defensive stale handler, but it is not a destination.
    assert CK.CB_AI_TTI not in NAVIGATOR


def test_every_keyboard_callback_is_resolvable():
    all_keyboards = {
        name: value
        for name, value in vars(kb).items()
        if isinstance(value, InlineKeyboardMarkup)
    }
    for name, markup in all_keyboards.items():
        for cb in _callbacks(markup):
            # nav:* -> NAVIGATOR; tasks/help/library -> their own
            # shared-chain handlers (views, not states); the rest must
            # match a tokenized action family
            assert (
                cb in NAVIGATOR
                or cb in {CK.CB_TASKS, CK.CB_HELP, CK.CB_LIBRARY}
                or any(p.match(cb) for p in ACTION_PATTERNS)
            ), f"{name} button -> {cb!r} has no handler"
            assert len(cb.encode("utf-8")) <= 64, (
                f"{name} callback exceeds 64 bytes: {cb!r}"
            )


def test_home_keyboard_exact_layout():
    """Spec §2.1 + music-library plan §5: exact row layout and styles
    (My music shares the row with Tasks; Help drops to its own row)."""
    rows = kb.home_keyboard.inline_keyboard
    assert len(rows) == 5
    assert [b.text for b in rows[0]] == [copy.BTN_SEARCH]
    assert rows[0][0].callback_data == "nav:search"
    assert rows[0][0].style == kb.ACTION_STYLE  # the one primary CTA
    assert [b.text for b in rows[1]] == [copy.BTN_INSTAGRAM, copy.BTN_YOUTUBE]
    assert [b.callback_data for b in rows[1]] == ["nav:ig", "nav:yt"]
    assert [b.text for b in rows[2]] == [copy.BTN_PINTEREST, copy.BTN_AI]
    assert [b.callback_data for b in rows[2]] == ["nav:pin", "nav:ai"]
    assert [b.text for b in rows[3]] == [copy.BTN_TASKS, copy.BTN_LIBRARY]
    assert [b.callback_data for b in rows[3]] == ["nav:tasks", "nav:library"]
    assert [b.text for b in rows[4]] == [copy.BTN_HELP]
    assert [b.callback_data for b in rows[4]] == ["nav:help"]
    for row in rows[1:]:  # everything else stays neutral
        for b in row:
            assert b.style is None


def test_input_screens_have_home_and_tasks():
    for keyboard in (
        kb.search_input_keyboard,
        kb.instagram_input_keyboard,
        kb.youtube_input_keyboard,
        kb.pinterest_input_keyboard,
    ):
        rows = keyboard.inline_keyboard
        assert len(rows) == 1
        assert [b.text for b in rows[0]] == [copy.BTN_HOME, copy.BTN_TASKS]
        assert [b.callback_data for b in rows[0]] == ["nav:home", "nav:tasks"]
    # AI chat has Home only (spec §2.2: no Tasks row there)
    rows = kb.ai_chat_keyboard.inline_keyboard
    assert [b.callback_data for b in rows[0]] == ["nav:home"]


def test_help_keyboard_layout():
    rows = kb.help_keyboard.inline_keyboard
    assert len(rows) == 2
    assert [b.callback_data for b in rows[0]] == ["nav:search", "nav:tasks"]
    assert [b.callback_data for b in rows[1]] == ["nav:home"]


# The new (current-rendering) navigation ids. The legacy aliases
# (nav:music, nav:yt:dl, nav:ai:chat, ...) are reachable only from
# pre-redesign messages, not from Home — they are wired (test 1) but
# deliberately not rendered anywhere.
NEW_NAV_CBS = {
    CK.CB_HOME, CK.CB_SEARCH, CK.CB_INSTAGRAM, CK.CB_PINTEREST,
    CK.CB_YOUTUBE, CK.CB_AI, CK.CB_TASKS, CK.CB_HELP,
}


def test_every_new_nav_callback_reachable_from_home():
    def collect(markup):
        return set(_callbacks(markup))

    seen: set[str] = set()
    frontier = list(collect(kb.home_keyboard))
    while frontier:
        cb = frontier.pop()
        if cb in seen:
            continue
        seen.add(cb)
        entry = NAVIGATOR.get(cb)
        if entry:
            frontier.extend(collect(entry[1]))
    # every new nav callback is wired and reachable from Home
    assert NEW_NAV_CBS <= seen
    # every legacy alias resolves through the navigator, not a screen
    assert (ALL_NAV_CBS - NEW_NAV_CBS) <= set(NAVIGATOR)


def test_navigator_entries_shape():
    for cb, (text, keyboard, state) in NAVIGATOR.items():
        assert (isinstance(text, str) and text.strip()) or callable(text), cb
        assert isinstance(keyboard, InlineKeyboardMarkup), cb
        assert isinstance(state, int), cb


def test_states_are_the_six_logical_modes():
    # Spec §2.5: six logical input modes; legacy names are aliases of them.
    assert (HOME_STATE, SEARCH_INPUT_STATE, INSTAGRAM_INPUT_STATE,
            YOUTUBE_INPUT_STATE, PINTEREST_INPUT_STATE, AI_CHAT_STATE) == tuple(range(6))
    from utils.constants.states import (
        START_STATE, MUSIC_STATE, MUSIC_SPOTIFY_STATE,
        INSTAGRAM_STATE, INSTAGRAM_DOWNLOAD_STATE,
        YOUTUBE_STATE, YOUTUBE_DOWNLOAD_STATE, YOUTUBE_MP3_STATE,
        PINTEREST_STATE, AI_STATE,
    )
    assert START_STATE == HOME_STATE
    assert MUSIC_STATE == SEARCH_INPUT_STATE
    assert MUSIC_SPOTIFY_STATE == SEARCH_INPUT_STATE
    assert INSTAGRAM_STATE == INSTAGRAM_INPUT_STATE
    assert INSTAGRAM_DOWNLOAD_STATE == INSTAGRAM_INPUT_STATE
    assert YOUTUBE_STATE == YOUTUBE_INPUT_STATE
    assert YOUTUBE_DOWNLOAD_STATE == YOUTUBE_INPUT_STATE
    assert YOUTUBE_MP3_STATE == YOUTUBE_INPUT_STATE
    assert PINTEREST_STATE == PINTEREST_INPUT_STATE
    assert AI_STATE == AI_CHAT_STATE


def test_navigator_routes_to_logical_modes():
    by_cb = {cb: entry[2] for cb, entry in NAVIGATOR.items()}
    assert by_cb[CK.CB_START] == HOME_STATE
    assert by_cb[CK.CB_HOME] == HOME_STATE
    assert by_cb[CK.CB_SEARCH] == SEARCH_INPUT_STATE
    assert by_cb[CK.CB_MUSIC] == SEARCH_INPUT_STATE
    assert by_cb[CK.CB_MUSIC_SPOTIFY] == SEARCH_INPUT_STATE
    assert by_cb[CK.CB_INSTAGRAM] == INSTAGRAM_INPUT_STATE
    assert by_cb[CK.CB_PINTEREST] == PINTEREST_INPUT_STATE
    assert by_cb[CK.CB_YOUTUBE] == YOUTUBE_INPUT_STATE
    assert by_cb[CK.CB_AI] == AI_CHAT_STATE
    # legacy one-hop-less buttons land in the same input screens
    assert by_cb[CK.CB_INSTAGRAM_DOWNLOAD] == INSTAGRAM_INPUT_STATE
    assert by_cb[CK.CB_YOUTUBE_DOWNLOAD] == YOUTUBE_INPUT_STATE
    assert by_cb[CK.CB_YOUTUBE_MP3] == YOUTUBE_INPUT_STATE


def test_yt_format_keyboard_exact_layout():
    """Spec §3.2: two video choices, MP3, Cancel — all neutral."""
    markup = kb.yt_format_keyboard("ab12cd34")
    rows = markup.inline_keyboard
    assert len(rows) == 3
    assert [b.text for b in rows[0]] == [copy.YT_FORMAT_720, copy.YT_FORMAT_360]
    assert [b.callback_data for b in rows[0]] == [
        "yt:fmt:ab12cd34:720", "yt:fmt:ab12cd34:360",
    ]
    assert [b.text for b in rows[1]] == [copy.YT_FORMAT_MP3]
    assert [b.callback_data for b in rows[1]] == ["yt:fmt:ab12cd34:mp3"]
    assert [b.callback_data for b in rows[2]] == ["yt:cancel:ab12cd34"]
    for row in rows:
        for b in row:
            assert b.style is None  # no recommended format (spec §15)


def test_track_keyboard_in_chat_and_inline():
    # in-chat carousel card: token + index, previous/next, search again, home
    markup = kb.track_keyboard("ab12cd34", index=1,
                               spotify_url="https://open.spotify.com/track/x")
    flat = _callbacks(markup)
    assert "song:dl:ab12cd34:1" in flat
    assert "song:prev:ab12cd34:1" in flat
    assert "song:prevcard:ab12cd34:1" in flat
    assert "song:next:ab12cd34:1" in flat
    assert "search:again:ab12cd34" in flat
    assert "nav:home" in flat
    # shared inline card: no index, no carousel row
    markup = kb.track_keyboard("ab12cd34", inline=True)
    flat = _callbacks(markup)
    assert "song:dl:ab12cd34" in flat
    assert "song:prev:ab12cd34" in flat
    assert not any("prevcard" in cb or "next" in cb for cb in flat)
    # absent Spotify URL -> no dead button
    assert not any(b.url for b in
                   (b for row in markup.inline_keyboard for b in row))
    markup = kb.track_keyboard("ab12cd34", index=0, spotify_url="")
    urls = [b for row in markup.inline_keyboard for b in row if b.url]
    assert urls == []


def test_too_large_and_large_file_keyboards():
    """Verify large file keyboards construct valid InlineKeyboardButton with copy_text."""
    from telegram import InlineKeyboardButton

    for markup in (kb.too_large_keyboard("https://example.com/file.mp4"),
                   kb.large_file_keyboard("https://example.com/file.mp4")):
        rows = markup.inline_keyboard
        assert len(rows) == 1
        btn = rows[0][0]
        assert isinstance(btn, InlineKeyboardButton)
        assert btn.text == copy.BTN_COPY_LINK
        d = btn.to_dict()
        assert d["copy_text"]["text"] == "https://example.com/file.mp4"
        assert rows[0][1].callback_data == "nav:home"
        assert rows[0][2].callback_data == "nav:tasks"
