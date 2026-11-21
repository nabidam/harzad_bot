"""Conversation states (UX redesign §2.5).

States are an input-routing detail, not a visible concept: each names the
"input mode" — what plain text means right now. The redesign collapses the
old five-button tree into six logical modes; there is no submenu hop
between a platform and its input screen.

Compatibility aliases keep the pre-redesign names importable during
rollout (handlers, tests and persisted in-memory states may still use them).
"""

HOME_STATE = 0
SEARCH_INPUT_STATE = 1
INSTAGRAM_INPUT_STATE = 2
YOUTUBE_INPUT_STATE = 3
PINTEREST_INPUT_STATE = 4
AI_CHAT_STATE = 5
# In-library search prompt (UX v3, plan §12.6): the next text message
# is a search of the user's own library — a different intent from
# SEARCH_INPUT (fresh Spotify search + download).
LIBRARY_SEARCH_STATE = 6

# --- compatibility aliases (legacy state names -> same logical modes) ---
START_STATE = HOME_STATE
MUSIC_STATE = SEARCH_INPUT_STATE
MUSIC_SPOTIFY_STATE = SEARCH_INPUT_STATE
INSTAGRAM_STATE = INSTAGRAM_INPUT_STATE
INSTAGRAM_DOWNLOAD_STATE = INSTAGRAM_INPUT_STATE
YOUTUBE_STATE = YOUTUBE_INPUT_STATE
YOUTUBE_DOWNLOAD_STATE = YOUTUBE_INPUT_STATE
YOUTUBE_MP3_STATE = YOUTUBE_INPUT_STATE
PINTEREST_STATE = PINTEREST_INPUT_STATE
AI_STATE = AI_CHAT_STATE
# Legacy TTI state: no longer reachable (dead feature removed, spec §10);
# the constant stays so old references import cleanly.
AI_TTI_STATE = 11
