from spotdl import Spotdl

SPOTIFY_CLIENT_ID = "5f573c9620494bae87890c0f08a60293"
SPOTIFY_CLIENT_SECRET = "212476d9b0f3472eaa762d90b19b0ba8"

spotdl = Spotdl(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)

songs = spotdl.search(["After dark", "https://open.spotify.com/track/2Fxmhks0bxGSBdJ92vM42m"])

# try:
#     loop = asyncio.get_event_loop()
# except RuntimeError:
#     loop = asyncio.new_event_loop()
#     asyncio.set_event_loop(loop)
        
# task = loop.create_task(user_insert_events(target))
# if not loop.is_running():
#     loop.run_until_complete(task)
    
print(songs)
# results = spotdl.download_songs(songs)
song, path = spotdl.download(songs[0])

print(song, path)