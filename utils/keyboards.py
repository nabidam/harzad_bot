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
from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

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


# --- Home (spec §2.1 row layout + 📚 My music, music-library plan §5) ----

home_keyboard = InlineKeyboardMarkup([
    [_action(copy.BTN_SEARCH, "nav:search")],
    [_button(copy.BTN_INSTAGRAM, "nav:ig"), _button(copy.BTN_YOUTUBE, "nav:yt")],
    [_button(copy.BTN_PINTEREST, "nav:pin"), _button(copy.BTN_AI, "nav:ai")],
    [_button(copy.BTN_TASKS, "nav:tasks"), _button(copy.BTN_LIBRARY, "nav:library")],
    [_button(copy.BTN_HELP, "nav:help")],
])

# --- Input screens (spec §2.2) ---------------------------------------------

search_input_keyboard = InlineKeyboardMarkup([_home_tasks_row()])
instagram_input_keyboard = InlineKeyboardMarkup([_home_tasks_row()])
youtube_input_keyboard = InlineKeyboardMarkup([_home_tasks_row()])
pinterest_input_keyboard = InlineKeyboardMarkup([_home_tasks_row()])
ai_chat_keyboard = InlineKeyboardMarkup([[_button(copy.BTN_HOME, "nav:home")]])


def ai_stop_keyboard(token: str) -> InlineKeyboardMarkup:
    """In-flight AI status row (spec §9): [⏹ Stop] [🏠 Home]. Stop is a
    real task cancel (task:cancel:<token>)."""
    return InlineKeyboardMarkup([
        [_action(copy.BTN_AI_STOP, f"task:cancel:{token}"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def ai_answer_keyboard() -> InlineKeyboardMarkup:
    """The AI answer row (spec §9): [Ask another question] [🏠 Home]."""
    return InlineKeyboardMarkup([
        [_button(copy.BTN_AI_ASK_AGAIN, "nav:ai:chat"),
         _button(copy.BTN_HOME, "nav:home")],
    ])

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


def task_cancel_keyboard(token: str, *, cancel_label: str | None = None) -> InlineKeyboardMarkup:
    """[⏹ Cancel] [📥 Tasks] on a running task status. `cancel_label`
    lets a flow use its own button copy (YouTube: "⏹ Cancel
    download", spec §6.3)."""
    return InlineKeyboardMarkup([
        [_button(cancel_label or copy.BTN_TASK_CANCEL, f"task:cancel:{token}"),
         _button(copy.BTN_TASKS, "nav:tasks")],
    ])


def retry_home_keyboard(token: str) -> InlineKeyboardMarkup:
    """[🔁 Retry] [🏠 Home] for a replayable input error (spec §7.2).
    Retry re-runs the task's stored payload under a NEW task token
    (spec §7.3)."""
    return InlineKeyboardMarkup([
        [_button(copy.BTN_RETRY, f"task:retry:{token}"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def retry_tasks_home_keyboard(token: str) -> InlineKeyboardMarkup:
    """[🔁 Retry] [📥 Tasks] [🏠 Home] — the temporary
    platform/proxy failure layout (spec §7.2)."""
    return InlineKeyboardMarkup([
        [_button(copy.BTN_RETRY, f"task:retry:{token}"),
         _button(copy.BTN_TASKS, "nav:tasks"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def too_large_keyboard(link: str) -> InlineKeyboardMarkup:
    """[📋 Copy download link] [🏠 Home] [📥 Tasks] on the >50 MB card
    (spec §6.3/§6.6). The copy button is an InlineKeyboardButton with
    CopyTextButton — it copies the direct link, no callback needed."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(copy.BTN_COPY_LINK, copy_text=CopyTextButton(link)),
         _button(copy.BTN_HOME, "nav:home"),
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
    """[📋 Copy download link] [🏠 Home] [📥 Tasks] for the >50 MB case (legacy alias)."""
    return too_large_keyboard(link)


# --- Music library, UX v3 (plan §12.2/§12.3) -----------------------------------
# One active card that morphs (in-place edits); every screen carries its
# own way back + the global escape row. Item-level buttons always carry
# the snapshot token + 1-based row (never the title — design §12.1:
# numbered association, no RTL/width breakage). No screen exceeds eight
# keyboard rows (design §4.3).

def library_menu_keyboard() -> InlineKeyboardMarkup:
    """M01 — the four Phase A destinations, then the escape row.
    Phase B inserts its `[🎼 Playlists]` row above the escape row
    (plan §12.1.3 — no placeholder before then)."""
    return InlineKeyboardMarkup([
        [_action(copy.BTN_LIB_LATEST, "lib:latest"),
         _button(copy.BTN_LIB_SEARCH, "lib:search")],
        [_button(copy.BTN_LIB_OFTEN, "lib:often"),
         _button(copy.BTN_LIB_BROWSE, "lib:browse")],
        [_button(copy.BTN_TASKS, "nav:tasks"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def library_list_keyboard(token: str, *, source: str, sort: str | None,
                          page: int, pages: int, count: int) -> InlineKeyboardMarkup:
    """M02 — one `[▶ n] [⋯ n]` row per track of the frozen page, then
    Previous/Next (pages>1), Menu, escape. Browse-family lists page via
    the stateless `lib:page:<source>:<n>` payload; search results use
    the tokenized `lib:spg:<token>:<n>` (the query can't ride a
    payload). At most 5+1+1+1 = 8 rows.

    The same keyboard is attached to re-sent audio so the actions keep
    working from the delivered message (plan §12.1.4).
    """
    rows: list[list[InlineKeyboardButton]] = [
        [_action(f"▶ {i}", f"lib:get:{token}:{i}"),
         _button(f"⋯ {i}", f"lib:more:{token}:{i}")]
        for i in range(1, count + 1)
    ]
    if pages > 1:
        page_row: list[InlineKeyboardButton] = []
        if source == "search":
            prev_cb = f"lib:spg:{token}:{page - 1}"
            next_cb = f"lib:spg:{token}:{page + 1}"
        else:
            # browse pages carry their sort (the order is part of the
            # page's identity); latest/often are fixed orders
            key = sort if source == "browse" else source
            prev_cb = f"lib:page:{key}:{page - 1}"
            next_cb = f"lib:page:{key}:{page + 1}"
        if page > 1:
            page_row.append(_button(copy.BTN_TRACK_PREVIOUS, prev_cb))
        if page < pages:
            page_row.append(_button(copy.BTN_TRACK_NEXT, next_cb))
        if page_row:
            rows.append(page_row)
    rows.append([_button(copy.BTN_LIB_MENU, "nav:library")])
    rows.append([_button(copy.BTN_TASKS, "nav:tasks"),
                 _button(copy.BTN_HOME, "nav:home")])
    return InlineKeyboardMarkup(rows)


def library_search_results_keyboard(token: str, *, page: int, pages: int,
                                    count: int,
                                    start: int = 1) -> InlineKeyboardMarkup:
    """M06 — same rows as M02 plus `[🔎 New search]`; on multi-page
    results the New-search button folds into the page row so the screen
    stays at eight rows (plan §12.1 adaptation). `start` is the first
    row number = the first result's position in the frozen full set, so
    the button labels match the printed numbers and the button index
    resolves into the snapshot's FULL track list."""
    rows: list[list[InlineKeyboardButton]] = [
        [_action(f"▶ {i}", f"lib:get:{token}:{i}"),
         _button(f"⋯ {i}", f"lib:more:{token}:{i}")]
        for i in range(start, start + count)
    ]
    if pages > 1:
        page_row: list[InlineKeyboardButton] = []
        if page > 1:
            page_row.append(_button(
                copy.BTN_TRACK_PREVIOUS, f"lib:spg:{token}:{page - 1}"))
        page_row.append(_button(copy.BTN_LIB_SEARCH_NEW, "lib:scn"))
        if page < pages:
            page_row.append(_button(
                copy.BTN_TRACK_NEXT, f"lib:spg:{token}:{page + 1}"))
        rows.append(page_row)
    else:
        rows.append([_button(copy.BTN_LIB_SEARCH_NEW, "lib:scn")])
    rows.append([_button(copy.BTN_LIB_MENU, "nav:library")])
    rows.append([_button(copy.BTN_TASKS, "nav:tasks"),
                 _button(copy.BTN_HOME, "nav:home")])
    return InlineKeyboardMarkup(rows)


def library_search_none_keyboard() -> InlineKeyboardMarkup:
    """M06 zero-result state."""
    return InlineKeyboardMarkup([
        [_action(copy.BTN_LIB_SEARCH_AGAIN, "lib:scn"),
         _button(copy.BTN_LIB_BROWSE, "lib:browse")],
        [_button(copy.BTN_LIB_MENU, "nav:library")],
        [_button(copy.BTN_TASKS, "nav:tasks"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def library_search_prompt_keyboard() -> InlineKeyboardMarkup:
    """M03 — the search-prompt card (its Cancel exits the conversation
    state; the TEXT handler owns the query)."""
    return InlineKeyboardMarkup([
        [_button(copy.BTN_LIB_SEARCH_CANCEL, "lib:scx")],
        [_button(copy.BTN_LIB_MENU, "nav:library")],
        [_button(copy.BTN_TASKS, "nav:tasks"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def library_sort_keyboard() -> InlineKeyboardMarkup:
    """M04 — Browse all sort selection (stateless callbacks)."""
    return InlineKeyboardMarkup([
        [_action(copy.BTN_LIB_SORT_RECENT, "lib:sort:recent")],
        [_button(copy.BTN_LIB_SORT_ADDED, "lib:sort:added")],
        [_button(copy.BTN_LIB_SORT_AZ, "lib:sort:az")],
        [_button(copy.BTN_LIB_MENU, "nav:library")],
        [_button(copy.BTN_TASKS, "nav:tasks"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def library_detail_keyboard(token: str,
                            spotify_url: str | None = None) -> InlineKeyboardMarkup:
    """M05 — one action per row (the detail is the management screen,
    design §5). "Open on Spotify" is a URL button when the row carries
    a link — it survives restarts and never goes stale (plan §12.1).
    Phase B inserts Add-to-playlist above Remove (8th row)."""
    rows: list[list[InlineKeyboardButton]] = [
        [_action(copy.BTN_LIB_GET_AGAIN, f"lib:dl:{token}")],
        [_action(copy.BTN_TRACK_PREVIEW, f"lib:pv:{token}")],
    ]
    if spotify_url:
        rows.append([_button(copy.BTN_LIB_SPOTIFY, url=spotify_url)])
    rows += [
        [_button(copy.BTN_LIB_REMOVE, f"lib:rm:{token}")],
        [_button(copy.BTN_LIB_BACK, f"lib:back:{token}")],
        [_button(copy.BTN_LIB_MENU, "nav:library")],
        [_button(copy.BTN_TASKS, "nav:tasks"),
         _button(copy.BTN_HOME, "nav:home")],
    ]
    return InlineKeyboardMarkup(rows)


def library_remove_confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    """M05b — remove confirmation (edits the detail card in place)."""
    return InlineKeyboardMarkup([
        [_action(copy.BTN_LIB_RM_YES, f"lib:rmc:{token}")],
        [_button(copy.BTN_LIB_KEEP, f"lib:keep:{token}")],
        [_button(copy.BTN_LIB_MENU, "nav:library")],
        [_button(copy.BTN_TASKS, "nav:tasks"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def library_often_empty_keyboard() -> InlineKeyboardMarkup:
    """M07 — no qualifying tracks yet: point at the other ways in."""
    return InlineKeyboardMarkup([
        [_action(copy.BTN_LIB_LATEST, "lib:latest"),
         _button(copy.BTN_LIB_SEARCH, "lib:search")],
        [_button(copy.BTN_LIB_MENU, "nav:library")],
        [_button(copy.BTN_TASKS, "nav:tasks"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def library_empty_keyboard() -> InlineKeyboardMarkup:
    """S0 — empty library: point at the global music search (the only
    way to fill it)."""
    return InlineKeyboardMarkup([
        [_action(copy.BTN_SEARCH, "nav:search"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def library_unavailable_keyboard() -> InlineKeyboardMarkup:
    """S1 — DB down: Try again re-opens the view (stateless callback)."""
    return InlineKeyboardMarkup([
        [_button(copy.BTN_LIB_TRY_AGAIN, "nav:library"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def library_no_resend_keyboard() -> InlineKeyboardMarkup:
    """Ladder exhausted: honest failure + the two ways back to the
    music (plan §12.2)."""
    return InlineKeyboardMarkup([
        [_action(copy.BTN_SEARCH, "nav:search"),
         _button(copy.BTN_LIBRARY, "nav:library"),
         _button(copy.BTN_HOME, "nav:home")],
    ])


def library_flood_wait_keyboard(original_callback: str) -> InlineKeyboardMarkup:
    """429 on a single re-send (plan §12.2 S1): the wait card's
    Try again is the ORIGINAL action's payload — re-tapping it before
    the window passes just gets another honest 429, never a bypass."""
    return InlineKeyboardMarkup([
        [_action(copy.BTN_LIB_TRY_AGAIN, original_callback),
         _button(copy.BTN_TASKS, "nav:tasks"),
         _button(copy.BTN_HOME, "nav:home")],
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
