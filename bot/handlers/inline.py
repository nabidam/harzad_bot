"""Inline query mode (plan 1.3 / 5.3): `@harzad_bot <song>` anywhere.

The user types the bot's name + a song in any chat (private, group,
channel they can post to). We answer with up to 3 track
InlineQueryResultArticles; tapping one delivers the track card (cover,
caption, [Download MP3] / [30s Preview] / [Lyrics] / [Open on Spotify]
buttons) into the chosen chat. From there the usual song callbacks take
over — the download is a background spotdl job into that chat, the
preview is the 30s iTunes/Deezer clip (plan 1.4).

Articles (not InlineQueryResultAudio) are deliberate: a full MP3 has no
direct URL — it is produced by spotdl on button press — so the
Audio result type (which needs a direct audio URL) can only ever carry
a 30s clip, while the article carries the full action set.

Results reuse the same 300s in-memory cache as the in-chat search
(plan 5.3), and the candidates are stashed in the user's user_data so
the delivered card's buttons resolve for that user.
"""
from logging import getLogger

from telegram import (
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode

from bot.handlers.music.spotify.search import CANDIDATES_KEY, track_caption
from connectors.spotify_search import CACHE_TTL_SECONDS, TrackResult, search_tracks
from utils import keyboards

logger = getLogger(__name__)

# Below this length Spotify search is noise; answer empty instead of
# burning an API call per keystroke.
MIN_QUERY_LENGTH = 2


def _to_article(index: int, track: TrackResult) -> InlineQueryResultArticle:
    description = " • ".join(
        p for p in (track.artist, track.album) if p
    )
    return InlineQueryResultArticle(
        id=str(index),
        # Bot API caps: title 1-64, description 0-256
        title=track.title[:64],
        description=description[:256] or "Unknown album",
        thumbnail_url=track.cover_url,
        input_message_content=InputTextMessageContent(
            message_text=track_caption(track),
            parse_mode=ParseMode.HTML,
        ),
        reply_markup=_card_markup(index, track.spotify_url),
    )


def _card_markup(index: int, spotify_url: str):
    """The same per-card buttons the in-chat search uses (plan 1.2)."""
    return keyboards.track_keyboard(index, spotify_url)


async def handler(update: Update, context):
    """InlineQueryHandler entry: answer with track articles."""
    iq = update.inline_query
    assert iq is not None
    query = (iq.query or "").strip()

    if len(query) < MIN_QUERY_LENGTH:
        await iq.answer([])
        return

    try:
        results = await search_tracks(query)
    except Exception:
        # backend hiccup: an empty answer is the honest response
        logger.warning("inline query search failed for %r", query, exc_info=True)
        await iq.answer([])
        return

    if not results:
        await iq.answer([])
        return

    # stash for the delivered card's buttons (per user, like the chat
    # search does) — a card sent into a group resolves for the user who
    # searched
    context.user_data[CANDIDATES_KEY] = results
    await iq.answer(
        [_to_article(i, t) for i, t in enumerate(results)],
        cache_time=CACHE_TTL_SECONDS,
    )
