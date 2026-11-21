# Inline callback data (UX redesign §16).
#
# Two families:
#  1. NAV callbacks name a destination screen/state and are stateless —
#     they work from any state and before /start. Legacy ids from the
#     pre-redesign menu tree are kept as aliases and mapped in
#     bot/navigation.py, so buttons on old messages keep working.
#  2. ACTION callbacks carry an 8-hex token that identifies the exact
#     original action (a specific URL, a specific result set, a specific
#     task). Tokens are opaque, short-lived and validated server-side; a
#     stale token gets a toast, never "the most recent thing".
#
# Complete callback payloads must stay ≤64 bytes (Bot API limit);
# utils/keyboards.py asserts this when building buttons.

# --- navigation (stateless) ---------------------------------------------------
CB_START: str = "nav:start"          # legacy alias of nav:home
CB_HOME: str = "nav:home"
CB_SEARCH: str = "nav:search"        # new: one-tap Search music
CB_INSTAGRAM: str = "nav:ig"         # now opens the input prompt directly
CB_INSTAGRAM_DOWNLOAD: str = "nav:ig:dl"   # legacy alias -> same
CB_MUSIC: str = "nav:music"          # legacy alias -> Search Music prompt
CB_MUSIC_SPOTIFY: str = "nav:music:spotify"  # legacy alias -> same
CB_PINTEREST: str = "nav:pin"
CB_YOUTUBE: str = "nav:yt"           # now opens the input prompt directly
CB_YOUTUBE_DOWNLOAD: str = "nav:yt:dl"   # legacy alias -> same (no forced format)
CB_YOUTUBE_MP3: str = "nav:yt:mp3"       # legacy alias -> same (no forced format)
CB_AI: str = "nav:ai"                # now opens AI Chat directly
CB_AI_CHAT: str = "nav:ai:chat"      # legacy alias -> same
CB_AI_TTI: str = "nav:ai:ttI"        # RETIRED: defensive stale handler only
CB_TASKS: str = "nav:tasks"          # new: task list view
CB_HELP: str = "nav:help"            # new: help page
CB_LIBRARY: str = "nav:library"      # new: My music view (data-driven card)

# --- search (spec §5) ----------------------------------------------------------
# New tokenized cancel; the pre-token 8-hex `srx:c:` payload stays
# accepted as a legacy alias (pattern below matches both).
SEARCH_CANCEL_CALLBACK_PATTERN: str = r"^(?:search:cancel|srx:c):([0-9a-f]{8})$"
SEARCH_AGAIN_CALLBACK_PATTERN: str = r"^search:again:([0-9a-f]{8})$"

# --- track cards (spec §5.3, §5.6) ---------------------------------------------
# Actions with a results token + index (in-chat carousel, owner-checked):
#   song:dl:<token>:<i>  song:prev:<token>:<i>  song:prevcard:..  song:next:..
# Shared inline track tokens carry NO index (the card IS the track):
#   song:dl:<token>  song:prev:<token>
# Groups: (1) action, (2) 8-hex token, (3) optional 1-based index.
SONG_ACTION_CALLBACK_PATTERN: str = r"^song:(dl|prev|prevcard|next):([0-9a-f]{8})(?::(\d+))?$"
# Pre-token legacy payloads (cards sent before this release): index only,
# small numbers (0..99). New token payloads are exactly 8 hex chars, so
# the two patterns never overlap. Legacy taps are unresolvable by design
# -> stale toast (spec §7, §16).
SONG_ACTION_LEGACY_PATTERN: str = r"^song:(dl|lyr|prev):(\d{1,2})$"

# --- YouTube format picker (spec §3.2) ------------------------------------------
# yt:fmt:<token>:<fmt> — the token names the exact URL, so two pickers can
# coexist and a new link never invalidates another picker.
YT_FMT_CALLBACK_PATTERN: str = r"^yt:fmt:([0-9a-f]{8}):(720|360|mp3)$"
YT_CANCEL_CALLBACK_PATTERN: str = r"^yt:cancel:([0-9a-f]{8})$"
# Legacy no-token payloads: cannot be resolved to a specific URL -> toast.
YT_FMT_LEGACY_PATTERN: str = r"^yt:fmt:(720|360|mp3)$"

# --- music library (music-library plan §5, §8) ----------------------------------
# One tokenized card per page snapshot (token -> frozen page, 300 s TTL):
#   lib:dl:<token>:<i>   lib:prev:<token>:<i>   lib:rm:<token>:<i>
#   lib:prevcard:<token>:<i>   lib:next:<token>:<i>     (in-card carousel)
#   lib:page:<n>                                              (DB re-read)
# Groups: (1) action, (2) 8-hex token, (3) 1-based track index.
LIB_ACTION_CALLBACK_PATTERN: str = r"^lib:(dl|prev|rm|prevcard|next):([0-9a-f]{8}):(\d+)$"
LIB_PAGE_CALLBACK_PATTERN: str = r"^lib:page:(\d+)$"

# --- tasks (spec §8) -------------------------------------------------------------
TASK_CANCEL_CALLBACK_PATTERN: str = r"^task:cancel:([0-9a-f]{8})$"
TASK_RETRY_CALLBACK_PATTERN: str = r"^task:retry:([0-9a-f]{8})$"
TASK_PAGE_CALLBACK_PATTERN: str = r"^task:page:(\d+)$"

# --- labels (button text lives in utils/copy.py; these are display-only) -------
SEARCH_CANCEL_BUTTON: str = "⏹ Cancel search"  # alias of copy.BTN_SEARCH_CANCEL
BACK_KEYBOARD: str = "Back 🔙"
HOME_KEYBOARD: str = "Home 🏘"
COPY_LINK_BUTTON: str = "📋 Copy link"

# Legacy labels kept for old messages / references.
INSTAGRAM_KEYBOARD: str = "Instagram"
INSTAGRAM_DOWNLOAD_KEYBOARD: str = "Download"
MUSIC_KEYBOARD: str = "Music 🎵"
MUSIC_SPOTIFY_KEYBOARD: str = "Spotify"
PINTEREST_KEYBOARD: str = "Pinterest"
YOUTUBE_KEYBOARD: str = "Youtube 🎞"
YOUTUBE_DOWNLOAD_KEYBOARD: str = "Download as video"
YOUTUBE_MP3_KEYBOARD: str = "Download as mp3"
AI_KEYBOARD: str = "AI 🤖"
AI_CHAT_KEYBOARD: str = "Chat 💬"
AI_TTI_KEYBOARD: str = "Text to image 🖼"
YT_FORMAT_720: str = "🎬 Video (720p)"
YT_FORMAT_360: str = "🎬 Video (360p)"
YT_FORMAT_MP3: str = "🎵 Audio MP3"
SONG_DL_BUTTON: str = "⬇️ Download MP3"
SONG_PREVIEW_BUTTON: str = "🎧 30s Preview"
SONG_LYR_BUTTON: str = "📄 Lyrics"
SONG_SPOTIFY_BUTTON: str = "🔗 Open on Spotify"
