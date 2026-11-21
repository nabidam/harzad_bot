"""The shared callback-handler chain (UX redesign §16).

One ordered spec, used both inside the conversation (every state) and in
group 1 for users without an active conversation. Order matters:

1. tokenized action callbacks (yt format picker, song cards, search
   cancel, task cancel/page) — most specific first;
2. the stateless navigator (nav:*) — patternless, last, answers every
   remaining callback so no client spinner ever hangs.

A callback must answer within 10 s (in practice: before any network
work); every handler here acknowledges first (spec §16 invariants).
"""
from collections.abc import Callable
from typing import Optional

from telegram.ext import CallbackQueryHandler

from bot.handlers import dispatch, help, tasks
from bot.handlers.music import library as music_library
from bot.handlers.music.spotify import search as spotify_search
from bot.navigation import navigate, on_ai_tti_stale, on_back
from utils.constants.keyboards import (
    CB_AI_TTI,
    CB_HELP,
    CB_LIBRARY,
    CB_TASKS,
    LIB_DETAIL_CALLBACK_PATTERN,
    LIB_LEGACY_ACTION_CALLBACK_PATTERN,
    LIB_LEGACY_PAGE_CALLBACK_PATTERN,
    LIB_PAGE_CALLBACK_PATTERN,
    LIB_ROW_CALLBACK_PATTERN,
    LIB_SEARCH_CANCEL_CALLBACK,
    LIB_SEARCH_NEW_CALLBACK,
    LIB_SEARCH_PAGE_CALLBACK_PATTERN,
    LIB_SEARCH_START_CALLBACK,
    LIB_SORT_CALLBACK_PATTERN,
    SONG_ACTION_CALLBACK_PATTERN,
    SONG_ACTION_LEGACY_PATTERN,
    SEARCH_AGAIN_CALLBACK_PATTERN,
    SEARCH_CANCEL_CALLBACK_PATTERN,
    TASK_CANCEL_CALLBACK_PATTERN,
    TASK_PAGE_CALLBACK_PATTERN,
    TASK_RETRY_CALLBACK_PATTERN,
    YT_CANCEL_CALLBACK_PATTERN,
    YT_FMT_CALLBACK_PATTERN,
    YT_FMT_LEGACY_PATTERN,
)

# (pattern or None, handler) — order is the dispatch order.
_SHARED_SPECS: list[tuple[Optional[str], Callable]] = [
    (YT_FMT_CALLBACK_PATTERN, dispatch.on_yt_format),
    (YT_CANCEL_CALLBACK_PATTERN, dispatch.on_yt_cancel),
    (YT_FMT_LEGACY_PATTERN, dispatch.on_yt_format_legacy),
    # Song-card buttons: tokenized payloads (result-set token, shared
    # across users) registered before the legacy index-only pattern per
    # spec §16. The two shapes never overlap (8-hex token vs 1-2 digit
    # index, both anchored), but the specific one must not sit behind
    # the legacy catch. Legacy taps are unresolvable by design -> toast.
    (SONG_ACTION_CALLBACK_PATTERN, spotify_search.on_song_action),
    (SONG_ACTION_LEGACY_PATTERN, spotify_search.on_song_action_legacy),
    (SEARCH_CANCEL_CALLBACK_PATTERN, spotify_search.on_search_cancel),
    (SEARCH_AGAIN_CALLBACK_PATTERN, spotify_search.on_search_again),
    # Music library, UX v3 (plan §12.3): tokenized row and detail
    # actions first (most specific), then search-result paging, the
    # stateless browse pages, sort rows and the exact menu /
    # search-state payloads; the legacy v2 patterns answer a defensive
    # stale toast (v2 cards may still sit in the live chat).
    # nav:library sits with the other view callbacks (Tasks/Help) — a
    # data-driven card, not a NAVIGATOR state.
    (LIB_ROW_CALLBACK_PATTERN, music_library.on_lib_row),
    (LIB_DETAIL_CALLBACK_PATTERN, music_library.on_lib_detail),
    (LIB_SEARCH_PAGE_CALLBACK_PATTERN, music_library.on_lib_search_page),
    (LIB_PAGE_CALLBACK_PATTERN, music_library.on_lib_page),
    (LIB_SORT_CALLBACK_PATTERN, music_library.on_lib_sort),
    (LIB_SEARCH_START_CALLBACK, music_library.on_lib_search_start),
    (LIB_SEARCH_NEW_CALLBACK, music_library.on_lib_search_new),
    (LIB_SEARCH_CANCEL_CALLBACK, music_library.on_lib_search_cancel),
    (r"lib:latest", music_library.on_lib_destination),
    (r"lib:often", music_library.on_lib_destination),
    (r"lib:browse", music_library.on_lib_destination),
    (CB_LIBRARY, music_library.on_library_callback),
    (LIB_LEGACY_ACTION_CALLBACK_PATTERN, music_library.on_lib_legacy),
    (LIB_LEGACY_PAGE_CALLBACK_PATTERN, music_library.on_lib_legacy),
    (TASK_CANCEL_CALLBACK_PATTERN, tasks.on_task_cancel),
    (TASK_RETRY_CALLBACK_PATTERN, tasks.on_task_retry),
    (TASK_PAGE_CALLBACK_PATTERN, tasks.on_task_page),
    (CB_TASKS, tasks.on_tasks_callback),
    (CB_HELP, help.on_help_callback),
    (r"^nav:back:.*", on_back),
    (CB_AI_TTI, on_ai_tti_stale),
    (None, navigate),
]


def shared_callback_specs() -> list[tuple[Optional[str], Callable]]:
    """The raw (pattern, handler) pairs — main.py guard-wraps these for
    group 1 without losing their patterns."""
    return list(_SHARED_SPECS)


def shared_callback_handlers(guard: Callable | None = None) -> list[CallbackQueryHandler]:
    """Build the handler objects for the current registration site.

    `guard` (the group-1 conversation guard in main.py) wraps each
    handler function; patterns are preserved.
    """
    handlers = []
    for pattern, fn in _SHARED_SPECS:
        if guard is not None:
            fn = guard(fn)
        handlers.append(CallbackQueryHandler(fn, pattern=pattern))
    return handlers
