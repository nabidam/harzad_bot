import asyncio
from spotdl import Spotdl
from configurations.settings import SPOTIFY_CLIENT_SECRET, SPOTIFY_CLIENT_ID

def create_spotdl_instance():
    # try:
    #     loop = asyncio.get_event_loop()
    # except RuntimeError:
    #     loop = asyncio.new_event_loop()
    #     asyncio.set_event_loop(loop)

    # download process wth spotdl
    try:
        spotdl = Spotdl(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
    except RuntimeError:
        spotdl = Spotdl()
    return spotdl
    # return Spotdl(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)

# spotdl = Spotdl(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)