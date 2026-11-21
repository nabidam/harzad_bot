"""Canonical user-facing copy deck (UX redesign spec §14).

Every user-facing string lives here under a stable key so handlers never
hard-code copy and P1 Persian localization has one place to override.
English is the canonical language for now.

Parse mode: message templates are HTML (see helpers.send_html_msg).
Anything user-provided ({query}, {title}, {artist}, {album}, {domain},
{first_name}, ...) must be escape_html()-ed BEFORE formatting.

Toasts (CallbackQuery.answer texts) are hard-capped by Telegram at
64 characters — the TOAST_* entries are kept under that and are plain
text, no tags.
"""

# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

# No numeric user ID, no bot ID. Both key jobs on one screen.
HOME_GREETING: str = (
    "Hi, {first_name} 👋\n"
    "Send any supported media link here to download it, or search for a song by name.\n"
    "You can paste a link anytime — no setup needed."
)

# Plain non-link text typed on Home (no input mode active):
# point at the two real actions, don't fake an error.
HOME_TEXT_HINT: str = (
    "Send a supported media link to download it,\n"
    "or tap <b>🔎 Search music</b> and type a song name."
)

BTN_SEARCH: str = "🔎 Search music"
BTN_INSTAGRAM: str = "📷 Instagram"
BTN_YOUTUBE: str = "▶️ YouTube"
BTN_PINTEREST: str = "📌 Pinterest"
BTN_AI: str = "🤖 AI chat"
BTN_TASKS: str = "📥 Tasks"
BTN_HELP: str = "❓ Help"
BTN_LIBRARY: str = "📚 My music"
BTN_HOME: str = "🏠 Home"
BTN_RETRY: str = "🔁 Retry"
BTN_RETRY_DOWNLOAD: str = "⏹ Cancel download"
BTN_COPY_LINK: str = "📋 Copy download link"
BTN_RETRY_FAILED: str = "🔁 Retry failed"
BTN_SHOW_MORE: str = "Show more"

# ---------------------------------------------------------------------------
# Input screens (spec §2.2)
# ---------------------------------------------------------------------------

SEARCH_PROMPT: str = (
    "Type a song title, artist, or both.\n"
    "Example: <code>Daft Punk Get Lucky</code>\n"
    "You can also paste a Spotify link anytime."
)

INSTAGRAM_PROMPT: str = (
    "Send an Instagram post, reel, or story link.\n"
    "For a profile picture, send a username like <code>@username</code>."
)

YOUTUBE_PROMPT: str = (
    "Send a YouTube video or playlist link.\n"
    "I'll ask you to choose 720p, 360p, or MP3."
)

PINTEREST_PROMPT: str = "Send a Pinterest video link."

AI_PROMPT: str = (
    "Ask me a question. I answer each message independently for now, "
    "so include any context I need in each message."
)

# ---------------------------------------------------------------------------
# Help (spec §13.1)
# ---------------------------------------------------------------------------

HELP_BODY: str = (
    "<b>How to use Harzad</b>\n\n"
    "• Paste a supported media link anytime to download it.\n"
    "• Tap <b>Search music</b> or type a song name from Search Music.\n"
    "• In any chat, type <code>@harzad_bot artist song</code> to find music inline.\n"
    "• Tap <b>Tasks</b> to check or cancel work that's still running.\n"
    "• Your requested songs live in <b>📚 My music</b> — bring any of them back anytime.\n"
    "• Use <b>AI chat</b> for a one-message question; each message is "
    "answered independently for now.\n\n"
    "Supported: YouTube, Instagram, Pinterest, TikTok, and Spotify. "
    "TikTok watermark removal isn't available yet.\n"
    "<i>AI chat is a lightweight local assistant, not a hosted GPT-class model.</i>"
)

# Bot profile (spec §13.3, set in post_init)
BOT_DESCRIPTION: str = (
    "Harzad downloads media from YouTube, Instagram, Pinterest, TikTok, "
    "and Spotify. Paste a link or search for a song by name. "
    "It also offers a lightweight AI chat."
)
BOT_SHORT_DESCRIPTION: str = "Media downloads, music search, and AI chat 🎧"

# ---------------------------------------------------------------------------
# Music search (spec §5)
# ---------------------------------------------------------------------------

SEARCH_WORKING: str = (
    "🔎 Searching for <b>{query}</b>\n"
    "Music search usually takes about 45 seconds. "
    "You can keep using Telegram while I search."
)

SEARCH_WORKING_TICK: str = (
    "🔎 Searching for <b>{query}</b>\n"
    "⏱ {elapsed}s elapsed. I'm still waiting for matches."
)

SEARCH_SLOW: str = (
    "🔎 Search is taking longer than usual for <b>{query}</b>.\n"
    "⏱ {elapsed}s elapsed. You can keep waiting or cancel."
)

SEARCH_VERY_SLOW: str = (
    "🔎 This search is slower than usual. I'll show results if they arrive.\n"
    "⏱ {elapsed}s elapsed."
)

SEARCH_CANCELLED: str = "⏹️ Search cancelled. Send another song name whenever you're ready."
SEARCH_TIMEOUT: str = (
    "I couldn't finish that search in time. Try again, "
    "or include both the artist and song title."
)

SEARCH_NO_RESULTS: str = (
    "No matches found for <b>{query}</b>.\n"
    "Try the artist and title together, for example Daft Punk Get Lucky."
)

SEARCH_FAILED: str = (
    "Music search is temporarily unavailable on our side. "
    "Your query is fine — please try again shortly."
)

BTN_SEARCH_CANCEL: str = "⏹ Cancel search"
BTN_SEARCH_AGAIN: str = "🔎 Search again"
BTN_SEARCH_ANOTHER: str = "🔎 Search another song"
BTN_RETRY_SEARCH: str = "🔁 Retry search"

# Toasts (≤64 chars)
TOAST_SEARCH_STALE: str = "This result expired. Search for the song again."
TOAST_SEARCH_CANCEL_STALE: str = "This search has already finished."
TOAST_TRACK_STALE: str = "This card expired. Search for the track again."

# ---------------------------------------------------------------------------
# Track cards (spec §5.3)
# ---------------------------------------------------------------------------

BTN_TRACK_DOWNLOAD: str = "⬇️ Download MP3"
BTN_TRACK_DOWNLOAD_INLINE: str = "⬇️ Send MP3 here"
BTN_TRACK_PREVIEW: str = "🎧 30s Preview"
BTN_TRACK_PREVIOUS: str = "◀ Previous"
BTN_TRACK_NEXT: str = "Next ▶"

TOAST_TRACK_NO_PREVIEW: str = "No 30-second preview is available."
TOAST_TRACK_FIRST: str = "Already showing the first match."
TOAST_TRACK_LAST: str = "That's the last match."


def track_caption(
    *,
    title: str,
    artist: str,
    album: str | None,
    duration: str | None,
    index: int,
    total: int,
) -> str:
    """HTML caption for one result card (spec §5.3, key `track.caption`).

    Missing album/duration omit their component (or the whole meta line),
    never render None or blank labels.
    """
    lines = [f"🎵 <b>{title}</b>", f"<i>{artist}</i>"]
    meta_parts = []
    if album:
        meta_parts.append(f"📀 {album}")
    if duration:
        meta_parts.append(f"⏱ {duration}")
    if meta_parts:
        lines.append(" · ".join(meta_parts))
    lines.append(f"Match {index} of {total}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Music library, UX v3 (docs/music-library-ux-design.md, plan §12)
# ---------------------------------------------------------------------------

# S0 empty library (no music yet — point at the global search).
LIB_NONE: str = (
    "No music saved yet.\n"
    "Your tracks will appear here automatically when you receive music."
)

# S1 database unavailable (distinct from S0 by design — plan §10.5).
LIB_UNAVAILABLE: str = (
    "⚠ My music is temporarily unavailable.\n"
    "Your library data could not be loaded.\n"
    "Please try again shortly."
)

# The re-send ladder exhausted (no stored cloud copy, no link to retry).
LIB_NO_RESEND: str = (
    "⚠ <b>Track unavailable</b>\n\n"
    "We couldn't retrieve this track.\n"
    "It may no longer be available from its source.\n"
    "You can search for it again."
)

# Flood control (429) on a single re-send: tell the user to wait; the
# wait card's Try again button is the ORIGINAL action's payload, so it
# can never bypass Telegram's retry window (plan §12.2 S1 row).
LIB_FLOOD_WAIT: str = (
    "⏳ <b>Telegram is limiting messages.</b>\n"
    "Please wait {seconds} seconds, then try again."
)

# M01 — My music menu.
LIB_MENU_TITLE: str = "📚 <b>Your music</b>"
LIB_MENU_COUNT: str = "{count} {word} saved"
LIB_MENU_DESC: str = "Everything you've received, ready to listen again."
LIB_MENU_PROMPT: str = "Choose where to start:"

# M02 — track list (latest / often / browse ×3 sorts / search results).
LIB_LIST_TITLE: str = "📚 <b>Your music</b> · {source}"
LIB_LIST_COUNT: str = "{count} {word}"
LIB_LIST_PAGE: str = " · Page {page} of {pages}"
LIB_LIST_PROMPT: str = "Select a track to listen again or view more actions."
LIB_LIST_REQUESTED: str = "Requested {count} times"
LIB_LIST_REQUESTED_ONCE: str = "Requested once"
LIB_LIST_LAST: str = "Last {time}"

# M02 source labels (list heading).
LIB_SOURCE_LATEST: str = "Latest"
LIB_SOURCE_OFTEN: str = "Often played"
LIB_SOURCE_RECENT: str = "Recently requested"
LIB_SOURCE_ADDED: str = "Recently added"
LIB_SOURCE_AZ: str = "Title A–Z"

# M03/M06 — library search.
LIB_SEARCH_TITLE: str = "🔎 Search your music"
LIB_SEARCH_PROMPT: str = "Send a track title or artist name."
LIB_SEARCH_HINT: str = "You can search using part of a title or artist."
LIB_SEARCH_CANCEL_HINT: str = "Send /cancel to stop searching."
LIB_SEARCH_TOO_SHORT: str = "Please enter at least 2 characters."
LIB_SEARCH_RESULTS: str = "🔎 Search results: <i>{query}</i>"
LIB_SEARCH_FOUND: str = "Found {count} {word}."
LIB_SEARCH_NONE: str = "🔎 No matching tracks"
LIB_SEARCH_NONE_DESC: str = "No tracks matched \"{query}\"."
LIB_SEARCH_NONE_TRY: str = "Try another title or artist name."

# M04 — Browse all sort selection.
LIB_BROWSE_TITLE: str = "📄 Browse all"
LIB_SORT_PROMPT: str = "Choose how your library should be ordered."

# M05 — track detail.
LIB_DETAIL_TITLE: str = "🎵 Track details"
LIB_DETAIL_ARTIST: str = "Artist: {artist}"
LIB_DETAIL_ALBUM: str = "Album: {album}"
LIB_DETAIL_DURATION: str = "Duration: {duration}"
LIB_DETAIL_REQUESTED: str = "Requested: {count} times"
LIB_DETAIL_LAST: str = "Last requested: {time}"
LIB_DETAIL_ADDED: str = "Added: {time}"
LIB_DETAIL_PROMPT: str = "What would you like to do?"

# M05b — remove confirmation (owner decision 2026-11-21, plan §12.1.2).
LIB_REMOVE_CONFIRM: str = (
    "🗑 Remove track?\n\n<b>{title}</b>\n{artist}\n"
    "This removes the track from My music.\n"
    "It does not delete previously sent messages."
)

# M07 — Often played, no qualifying tracks yet.
LIB_OFTEN_EMPTY: str = (
    "⭐ <b>Often played</b>\n\n"
    "Your frequently requested tracks will appear here as you listen again."
)

# Buttons.
BTN_LIB_LATEST: str = "🕒 Latest"
BTN_LIB_SEARCH: str = "🔎 Search"
BTN_LIB_OFTEN: str = "⭐ Often played"
BTN_LIB_BROWSE: str = "📄 Browse all"
BTN_LIB_MENU: str = "📚 Menu"
BTN_LIB_GET_AGAIN: str = "▶ Get again"
BTN_LIB_SPOTIFY: str = "🎵 Open on Spotify"
BTN_LIB_REMOVE: str = "🗑 Remove from library"
BTN_LIB_BACK: str = "◀ Back to tracks"
BTN_LIB_TRY_AGAIN: str = "🔁 Try again"
BTN_LIB_RM_YES: str = "🗑 Yes, remove"
BTN_LIB_KEEP: str = "◀ Keep track"
BTN_LIB_SEARCH_CANCEL: str = "✖ Cancel"
BTN_LIB_SEARCH_NEW: str = "🔎 New search"
BTN_LIB_SEARCH_AGAIN: str = "🔎 Search again"
BTN_LIB_SORT_RECENT: str = "🕒 Recently requested"
BTN_LIB_SORT_ADDED: str = "📅 Recently added"
BTN_LIB_SORT_AZ: str = "🔤 Title A–Z"

# Toasts (≤64 chars)
TOAST_LIB_STALE: str = "This list has expired. Open My music and try again."
TOAST_LIB_REMOVED: str = "Track removed from My music."
TOAST_LIB_FAILED: str = "That didn't work — please try again."
TOAST_LIB_PAGE: str = "That page doesn't exist anymore."
TOAST_LIB_PRIVATE: str = "My music is available in private chat only."
TOAST_LIB_NOT_FOUND: str = "This track is no longer in your library."
TOAST_LIB_SEARCH_CANCELLED: str = "Search cancelled."


# ---------------------------------------------------------------------------
# Task / wait design system (spec §4)
# ---------------------------------------------------------------------------

TASK_ACCEPTED: str = "📥 Got it — checking your link…"
TASK_RESOLVING: str = (
    "🔎 Getting media details…\n⏱ {elapsed}s elapsed. You can cancel anytime."
)
TASK_SLOW: str = (
    "🔎 This is taking longer than usual. I'm still trying.\n⏱ {elapsed}s elapsed."
)
TASK_DOWNLOADING: str = "⬇️ Downloading {short_title}…\n⏱ {elapsed}s elapsed."
TASK_STILL_WORKING: str = (
    "⬇️ Still working on {short_title}.\n"
    "⏱ {elapsed}s elapsed. No reliable finish time is available yet."
)
TASK_UPLOADING: str = "📤 Sending {short_title} to Telegram…"
TASK_ITEM_UPLOADING: str = "📤 Sending item {current}/{total}…"

TASK_CANCEL_REQUESTED: str = (
    "⏹️ Cancellation requested. I'll stop as soon as the current step "
    "can be interrupted."
)
TASK_CANCELLED: str = (
    "⏹️ Cancelled. {completed_count} file(s) already sent remain in the chat."
)
TASK_AI_CANCELLED: str = "⏹️ Stopped. No answer was sent."
TASK_PLAYLIST_DONE: str = (
    "✅ Playlist finished: {success_count}/{total_count} tracks sent.{failure_line}"
)
TASK_PLAYLIST_FAILED_LINE: str = "\n{failed_count} track(s) couldn't be downloaded."
TASK_PLAYLIST_CANCELLED: str = (
    "⏹️ Playlist cancelled. {success_count}/{total_count} tracks were sent; "
    "those files remain in the chat."
)

TASK_NONE: str = (
    "No active tasks right now.\n"
    "Send a supported link to download media, or search for a song by name."
)

TASK_SEVERAL: str = "Several tasks are running — pick one below."

BTN_TASK_CANCEL: str = "⏹ Cancel"
BTN_TASK_STOPPING: str = "⏹ Stopping…"

# Toasts (≤64 chars)
TOAST_TASK_STALE: str = "This task has already finished."

# Per-platform wait phases (spec §6)
SPOTIFY_ACCEPTED: str = "📥 Got it — checking this Spotify link…"
SPOTIFY_TRACK: str = (
    "⬇️ Downloading track {current}/{total}: {title}\n⏱ {elapsed}s elapsed."
)
SPOTIFY_SLOW: str = (
    "This download is taking longer than usual. "
    "I'm still working on track {current}/{total}."
)
SPOTIFY_VERY_SLOW: str = (
    "Still downloading {title}. There's no reliable finish time available yet."
)
# The upload body left our side, but Telegram's confirmation timed out:
# the file is still being relayed and will appear (possibly late).
SPOTIFY_SENT_IN_FLIGHT: str = (
    "📤 The file is on its way — this connection is slow, so it may "
    "take a moment to appear."
)

INSTAGRAM_ACCEPTED: str = "📥 Got it — checking this Instagram link…"
INSTAGRAM_RESOLVING: str = "🔎 Getting Instagram media details…\n⏱ {elapsed}s elapsed."
INSTAGRAM_SLOW: str = (
    "Instagram is taking longer than usual to respond. I'm still trying.\n"
    "⏱ {elapsed}s elapsed."
)
INSTAGRAM_STORY: str = "🔎 Getting Story details…"

YOUTUBE_ACCEPTED: str = "📥 Got it — checking this YouTube link…"
YOUTUBE_RESOLVING: str = "🔎 Getting video details…\n⏱ {elapsed}s elapsed."
YOUTUBE_SLOW: str = (
    "YouTube is taking longer than usual to load this video. I'm still trying.\n"
    "⏱ {elapsed}s elapsed."
)

PINTEREST_ACCEPTED: str = "📥 Got it — checking this Pinterest link…"
PINTEREST_RESOLVING: str = (
    "🔎 Getting Pinterest media details…\n⏱ {elapsed}s elapsed."
)

# ---------------------------------------------------------------------------
# YouTube format picker (spec §3.2)
# ---------------------------------------------------------------------------

YT_FORMAT_PROMPT: str = "Choose a format for this YouTube link."
YT_FORMAT_720: str = "🎬 Video · 720p"
YT_FORMAT_360: str = "🎬 Video · 360p"
YT_FORMAT_MP3: str = "🎵 Audio · MP3"
BTN_YT_CANCEL: str = "✖ Cancel"

TOAST_YT_STALE: str = "This link expired. Send it again."

# ---------------------------------------------------------------------------
# AI chat (spec §9)
# ---------------------------------------------------------------------------

AI_THINKING: str = "🤖 Thinking about your question…"
AI_WORKING: str = "🤖 Working on your answer…\n⏱ {elapsed}s elapsed."
AI_SLOW: str = (
    "🤖 This is taking a little longer than usual.\n⏱ {elapsed}s elapsed."
)
AI_TIMEOUT: str = (
    "I couldn't finish that answer in time. Please try again with a shorter question."
)

BTN_AI_STOP: str = "⏹ Stop"
BTN_AI_ASK_AGAIN: str = "Ask another question"

# ---------------------------------------------------------------------------
# Errors (spec §7)
# ---------------------------------------------------------------------------

ERROR_INVALID_LINK: str = (
    "That doesn't look like a supported media link. "
    "Send a link from YouTube, Instagram, Pinterest, TikTok, or Spotify."
)

ERROR_UNSUPPORTED_DOMAIN: str = (
    "I can't download from {domain} yet. "
    "I support YouTube, Instagram, Pinterest, TikTok, and Spotify."
)

ERROR_MEDIA_UNAVAILABLE: str = (
    "I couldn't access this media. Check that it still exists and is "
    "available, then try again."
)

ERROR_TEMPORARY_SERVICE: str = (
    "{platform} is temporarily unavailable on our side. Please try again shortly."
)

ERROR_INSTAGRAM_SESSION: str = (
    "Instagram access is temporarily unavailable on our side. "
    "Your link may be fine — please try again later."
)

ERROR_INSTAGRAM_NOT_FOUND: str = (
    "I couldn't access that Instagram post. Check that the link is correct "
    "and the content is available, then try again."
)

ERROR_INSTAGRAM_USER_NOT_FOUND: str = (
    "I couldn't find that Instagram profile. Check the username and try again."
)

ERROR_AI_TIMEOUT: str = AI_TIMEOUT

ERROR_UNEXPECTED: str = "Something went wrong on our side. Please try again."

ERROR_RESTART: str = (
    "This action expired after a restart. "
    "Send the link or search again to start a new task."
)

ERROR_TOO_LARGE: str = (
    "This file is too large to send in Telegram. "
    "You can download it from the link below.\n"
    "Check the link's expiry before sharing it."
)
ERROR_TOO_LARGE_NO_KNOWN_EXPIRY: str = (
    "The link may stop working if the temporary file is removed."
)

ERROR_INVALID_YOUTUBE: str = (
    "That doesn't look like a supported YouTube video or playlist link. "
    "Check the URL and send it again."
)

ERROR_INVALID_PINTEREST: str = (
    "I couldn't recognize that Pinterest link. "
    "Send the link to a Pinterest video or Pin and try again."
)

# Defensive toasts for retired buttons (spec §10, ≤64 chars)
TOAST_IMAGE_GEN_UNAVAILABLE: str = "Image generation isn't available yet. Try AI chat."
TOAST_LYRICS_UNAVAILABLE: str = "Lyrics aren't available yet."

# ---------------------------------------------------------------------------
# Inline search (spec §5.6)
# ---------------------------------------------------------------------------

INLINE_MIN_QUERY_TITLE: str = "Search for a song"
INLINE_MIN_QUERY_DESC: str = "Type an artist or song title after @harzad_bot."
INLINE_NO_RESULTS_TITLE: str = "No matches"
INLINE_NO_RESULTS_DESC: str = "Try the artist and title together."
INLINE_FAILED_TITLE: str = "Search failed"

# ---------------------------------------------------------------------------
# Mini App copy (spec §12.3)
# ---------------------------------------------------------------------------

MINI_DOWNLOAD_PLACEHOLDER: str = "Paste a media link to download it."
MINI_SUPPORTED_PLATFORMS: str = "YouTube · Instagram · Pinterest · TikTok · Spotify"
MINI_DOWNLOAD_SUBMIT: str = "Download"
MINI_DOWNLOAD_LOADING: str = "Checking link…"
MINI_TASKS_EMPTY: str = "No tasks yet."
MINI_DOWNLOAD_FAILED: str = "This download failed. Check the link or try again."
MINI_RETRY: str = "Try again"
MINI_LINK_EXPIRED: str = (
    "This download link may have expired. Generate a new one to continue."
)
