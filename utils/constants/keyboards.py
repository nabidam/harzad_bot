# Inline callback data (Phase A): machine-readable navigation ids.
# A callback id always names the destination state, so the same
# dispatcher works no matter which state the user clicks from.
CB_START: str = "nav:start"
CB_INSTAGRAM: str = "nav:ig"
CB_INSTAGRAM_DOWNLOAD: str = "nav:ig:dl"
CB_MUSIC: str = "nav:music"
CB_MUSIC_SPOTIFY: str = "nav:music:spotify"
CB_PINTEREST: str = "nav:pin"
CB_YOUTUBE: str = "nav:yt"
CB_YOUTUBE_DOWNLOAD: str = "nav:yt:dl"
CB_YOUTUBE_MP3: str = "nav:yt:mp3"
CB_AI: str = "nav:ai"
CB_AI_CHAT: str = "nav:ai:chat"
CB_AI_TTI: str = "nav:ai:ttI"

# Universal dispatcher: YouTube format selector (plan 1.1). The clicked
# format downloads the URL the dispatcher stashed in user_data.
CB_YT_FMT_720: str = "yt:fmt:720"
CB_YT_FMT_360: str = "yt:fmt:360"
CB_YT_FMT_MP3: str = "yt:fmt:mp3"
YT_FMT_CALLBACK_PATTERN: str = r"^yt:fmt:(?:720|360|mp3)$"

# Spotify search track cards (plan 1.2): the buttons act on the candidates
# stashed in user_data["song_candidates"] when the cards were rendered —
# callback_data can only carry the card index, not the track itself.
# Groups: (1) action, (2) candidate index.
SONG_ACTION_CALLBACK_PATTERN: str = r"^song:(dl|lyr|prev):(\d+)$"

# main keyboards
START_KEYBOARD: str = "START"
FIRST_KEYBOARD: str = "First"
SECOND_KEYBOARD: str = "Second"
FINAL_KEYBOARD: str = "Final"

BACK_KEYBOARD: str = "Back 🔙"

HOME_KEYBOARD: str = "Home 🏘"

COPY_LINK_BUTTON: str = "📋 Copy link"

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

# YouTube format selector (universal dispatcher, plan 1.1)
YT_FORMAT_720: str = "🎬 Video (720p)"
YT_FORMAT_360: str = "🎬 Video (360p)"
YT_FORMAT_MP3: str = "🎵 Audio MP3"

# Spotify search cards (plan 1.2)
SONG_DL_BUTTON: str = "⬇️ Download MP3"
SONG_PREVIEW_BUTTON: str = "🎧 30s Preview"
SONG_LYR_BUTTON: str = "📄 Lyrics"
SONG_SPOTIFY_BUTTON: str = "🔗 Open on Spotify"