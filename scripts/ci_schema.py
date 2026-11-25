"""Create all model tables on the CI test database (idempotent).

The user/log tables have alembic migrations; the music-library tables
(tracks/playlists/playlist_items/tags/track_tags) get theirs in
migrations/versions/b3e5f7a9c1d2 (guarded create — a fresh
`alembic upgrade head` builds the same schema), so create_all from the
models is the single source of truth for the test schema.

Run from the repo root (the workflow does this):  uv run python scripts/ci_schema.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))  # env_pins

# Same env bootstrap as tests/conftest.py (no pytest here).
import env_pins  # noqa: E402  (tests/ is importable via repo-root path below)
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.ci")
os.environ.setdefault(
    "DB_URI", "postgresql+psycopg2://baaham:baaham_password@127.0.0.1:5432/harzad_db"
)
env_pins.apply(ROOT)

import models.playlist
import models.playlist_item
import models.log
import models.tag
import models.track
import models.track_tag
import models.user  # noqa: F401  (imported for table registration)

from connectors.db import Base, engine

Base.metadata.create_all(engine)
print(f"schema ready: {engine.url.render_as_string(hide_password=True)}")
