import os
from configurations.settings import ADMIN_TELEGRAM_USER_ID, INSTAGRAM_PASSWORD, INSTAGRAM_USER, PROXY_LIST
from utils.InstagramClient import InstagramClient
from utils.constants import LOGIN
from utils.exceptions.instagram import LoginException
from logging import getLogger

# Init logger
logger = getLogger(__name__)

def get_user_client_instagram():
    logger.info("Login Admin User")
    current_directory = os.getcwd()
    login_directory = f"{current_directory}/{LOGIN.lower()}"
    user_instagram_session_name = (f"{INSTAGRAM_USER}_{ADMIN_TELEGRAM_USER_ID}.json")
    user_instagram_session_path = f"{login_directory}/{user_instagram_session_name}"
    try:
        client = InstagramClient(username=INSTAGRAM_USER, password=INSTAGRAM_PASSWORD).get_client(
            login_directory=login_directory,
            telegram_user_id=ADMIN_TELEGRAM_USER_ID,
            user_instagram_session=user_instagram_session_path,
            save_session=True,
        )
        return client
    except LoginException:
        os.remove(user_instagram_session_path)
        pass

    try:
        client = InstagramClient(username=INSTAGRAM_USER, password=INSTAGRAM_PASSWORD).get_client(
            login_directory=login_directory,
            telegram_user_id=ADMIN_TELEGRAM_USER_ID,
            user_instagram_session=user_instagram_session_path,
            save_session=True,
        )
        return client
    except LoginException:
        pass