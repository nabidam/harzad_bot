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
WEBHOOK = False
# The following configuration is only needed if you setted WEBHOOK to True #
WEBHOOK_OPTIONS = {
    "listen": "0.0.0.0",  # IP
    "port": 443,
    "url_path": TOKEN,  # This is recommended for avoiding random people
    # making fake updates to your bot
    "webhook_url": f"https://example.com/{TOKEN}",
}
