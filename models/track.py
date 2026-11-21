from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    String,
    TIMESTAMP,
    Text,
    UniqueConstraint,
    text,
)

from connectors.db import Base


class Track(Base):
    """One requested song in a user's music library (music-library plan §4).

    Metadata only — no media bytes live on this box. `file_id` + the
    original message refs power the cloud re-send ladder (plan §3):
    resend-by-file_id first, copyMessage second, re-download last.
    """

    __tablename__ = "tracks"
    __table_args__ = (UniqueConstraint("tg_id", "track_key"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tg_id = Column(Integer, nullable=False)
    # Spotify track id when known; sha1(title|artist|album) otherwise.
    track_key = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False, default="")
    album = Column(Text, nullable=False, default="")
    duration_sec = Column(Integer, nullable=True)
    cover_url = Column(Text, nullable=True)
    spotify_url = Column(Text, nullable=True)
    source = Column(String(32), nullable=False, default="spotify")
    # Telegram cloud handles of the last delivery (None: in-flight
    # timeout — no usable send response, plan §3 known gap).
    file_id = Column(Text, nullable=True)
    orig_chat_id = Column(BigInteger, nullable=True)
    orig_message_id = Column(BigInteger, nullable=True)
    request_count = Column(Integer, nullable=False, default=1)
    added_on = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    last_requested = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
