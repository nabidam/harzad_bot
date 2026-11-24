"""Create all model tables on the CI test database (idempotent).

The user/log tables have alembic migrations; the music-library tables
(tracks/playlists/playlist_items) do not yet, so create_all from the
models is the single source of truth for the test schema.
"""
import models.playlist
import models.playlist_item
import models.log
import models.track
import models.user  # noqa: F401  (imported for table registration)

from connectors.db import Base, engine

Base.metadata.create_all(engine)
