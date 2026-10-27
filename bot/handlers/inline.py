"""Inline query mode (redesign §5.6): `@harzad_bot <song>` anywhere.

The user types the bot's name + a song in any chat (private, group,
channel they can post to). We answer with up to 3 track
InlineQueryResultArticles; tapping one delivers the track card (cover,
caption, [Send MP3 here] / [30s Preview] / [Open on Spotify] buttons)
into the chosen chat. From there the usual song callbacks take over —
the download is a background spotdl job into that chat, the preview is
the 30s iTunes/Deezer clip.

Group-safety (spec §5.6): each article carries its own immutable
single-track token in the shared results store, so the delivered card
resolves for ANY user who taps it, not only the searcher, and never
shows another user's private state. An expired shared token answers
the stale toast and runs nothing.

Articles (not InlineQueryResultAudio) are deliberate: a full MP3 has no
direct URL — it is produced by spotdl on button press — so the
Audio result type (which needs a direct audio URL) can only ever carry
a 30s clip, while the article carries the full action set.

Too-short queries and backend failures answer a helpful card instead
of a silent empty result (spec §5.6). Results reuse the same 300 s
in-memory cache as the in-chat search.
"""
from logging import getLogger

from telegram import (
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode

from connectors.spotify_search import CACHE_TTL_SECONDS, TrackResult, search_tracks
from utils import copy, keyboards
from utils.helpers import escape_html
from utils.tokens import get_token_store

from bot.handlers.music.spotify import search as spotify_search

logger = getLogger(__name__)

# Below this length Spotify search is noise; answer the help card
# instead of burning an API call per keystroke (spec §5.6).
MIN_QUERY_LENGTH = 2


def _help_article() -> InlineQueryResultArticle:
    """The help card for too-short queries (spec §5.6 exact copy)."""
    return InlineQueryResultArticle(
        id="harzad-help",
        title=copy.INLINE_MIN_QUERY_TITLE,
        description=copy.INLINE_MIN_QUERY_DESC,
        input_message_content=InputTextMessageContent(
            message_text=copy.SEARCH_PROMPT,
            parse_mode=ParseMode.HTML,
        ),
    )


def _no_results_article(query: str) -> InlineQueryResultArticle:
    """A useful explanation when the query matched nothing."""
    return InlineQueryResultArticle(
        id="harzad-no-results",
        title="No matches",
        description="Try the artist and title together.",
        input_message_content=InputTextMessageContent(
            message_text=copy.SEARCH_NO_RESULTS.format(query=escape_html(query)),
            parse_mode=ParseMode.HTML,
        ),
    )


def _failed_article() -> InlineQueryResultArticle:
    """The honest backend-failure card (spec §5.6)."""
    return InlineQueryResultArticle(
        id="harzad-search-failed",
        title="Search failed",
        description=copy.SEARCH_FAILED[:256],
        input_message_content=InputTextMessageContent(
            message_text=copy.SEARCH_FAILED,
            parse_mode=ParseMode.HTML,
        ),
    )


def _to_article(track: TrackResult, token: str) -> InlineQueryResultArticle:
    """One result as an article; the card's buttons resolve through the
    article's own single-track token (spec §5.6)."""
    description = " • ".join(
        p for p in (track.artist, track.album) if p
    )
    return InlineQueryResultArticle(
        id=token,  # unique per answer: fresh token per track
        # Bot API caps: title 1-64, description 0-256
        title=track.title[:64],
        description=description[:256] or "Unknown album",
        thumbnail_url=track.cover_url,
        input_message_content=InputTextMessageContent(
            message_text=spotify_search.card_caption(track, 1, 1),
            parse_mode=ParseMode.HTML,
        ),
        # inline card: no index (the card IS the track), "Send MP3 here"
        # label so the group destination is clear (spec §5.6)
        reply_markup=keyboards.track_keyboard(token, inline=True),
    )


async def handler(update: Update, context):
    """InlineQueryHandler entry: answer with track articles."""
    iq = update.inline_query
    assert iq is not None
    query = (iq.query or "").strip()

    if len(query) < MIN_QUERY_LENGTH:
        await iq.answer([_help_article()])
        return

    try:
        results = await search_tracks(query)
    except Exception:
        # backend hiccup: a useful explanation, never a silent nothing
        # (spec §5.6)
        logger.warning("inline query search failed for %r", query, exc_info=True)
        await iq.answer([_failed_article()])
        return

    if not results:
        await iq.answer([_no_results_article(query)])
        return

    # One immutable single-track token per article in the shared store:
    # the delivered card works for any user who taps it, in any chat
    # (spec §5.6), for the same 300 s the in-chat cards live.
    store = get_token_store(context, spotify_search.RESULTS_STORE)
    articles = [
        _to_article(track, store.put(
            {"query": query, "tracks": [track]}, spotify_search.RESULTS_TTL_SECONDS
        ))
        for track in results
    ]
    await iq.answer(articles, cache_time=CACHE_TTL_SECONDS)
