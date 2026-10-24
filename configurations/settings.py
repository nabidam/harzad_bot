import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["TOKEN"]
NAME = os.environ["BOT_NAME"]
SQLITE = os.environ["SQLITE"]
ADMINS = os.environ["ADMINS"].split(',')
SPOTIFY_CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
PROXY_LIST = os.environ["PROXY_LIST"]
ADMIN_TELEGRAM_USER_ID = os.environ["ADMIN_TELEGRAM_USER_ID"]
INSTAGRAM_USER = os.environ["INSTAGRAM_USER"]
INSTAGRAM_PASSWORD = os.environ["INSTAGRAM_PASSWORD"]
DOWNLOAD_MP3_PATH = os.environ["DOWNLOAD_MP3_PATH"]
DOWNLOAD_VIDEO_PATH = os.environ["DOWNLOAD_VIDEO_PATH"]
HOSTNAME = os.environ["HOSTNAME"]
HOST_ROOT = os.environ["HOST_ROOT"]
SCRIPT_DIR = os.environ["SCRIPT_DIR"]
WEBHOOK = False
# The following configuration is only needed if you setted WEBHOOK to True #
WEBHOOK_OPTIONS = {
    "listen": "0.0.0.0",  # IP
    "port": 443,
    "url_path": TOKEN,  # This is recommended for avoiding random people
    # making fake updates to your bot
    "webhook_url": f"https://example.com/{TOKEN}",
}

# Local LLM backend: any OpenAI-compatible server (vLLM, Ollama, LM Studio,
# ...). LLM_BASE_URL points at the /v1 endpoint; LLM_MODEL must match an id
# the server reports in GET {LLM_BASE_URL}/models. LLM_API_KEY may be a dummy
# string for local servers that don't authenticate.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:40002/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "not-needed")
LLM_MODEL = os.environ.get("LLM_MODEL", "gemma4")
HF_TOKEN = os.environ["HF_TOKEN"]
REPLICATE_API_TOKEN = os.environ["REPLICATE_API_TOKEN"]
TOGETHER_API_KEY = os.environ["TOGETHER_API_KEY"]

DB_URI = os.environ["DB_URI"]

YT_COOKIE_FILE = os.environ["YT_COOKIE_FILE"]
YT_PROXY = os.environ["YT_PROXY"]

# --- Mini App (aiohttp server embedded in the bot process) ---
# The app serves the SPA and the API from one origin — Bot API 10.2 locks
# WebApp methods to the registered Mini App domain, so they must share it.
WEB_HOST = os.environ.get("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("WEB_PORT", "8377"))
# Public HTTPS URL where the Mini App is reachable (e.g. a cloudflared
# tunnel). Drives the chat menu button and absolute file-download URLs.
# Empty = Mini App disabled, bot runs as before.
MINIAPP_URL = os.environ.get("MINIAPP_URL", "")