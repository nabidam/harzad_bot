from sqlalchemy import (
    BigInteger,
    Column,
    Index,
    Integer,
    String,
    TIMESTAMP,
    text,
)

from connectors.db import Base


class Playlist(Base):
    """A named user collection of library tracks (music-library plan §4,
    WebApp v2 spec §4.1).

    The name is unique per user case-insensitively — enforced as a
    unique index on (tg_id, lower(name)); a unique CONSTRAINT cannot
    carry the expression under SQLAlchemy 2.1's declarative.
    `updated_on` moves on every rename or membership change
    (the SPA's sort signal).
    """

    __tablename__ = "playlists"
    __table_args__ = (
        Index("uq_playlists_tg_lower_name", "tg_id", text("lower(name)"),
              unique=True),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tg_id = Column(Integer, nullable=False)
    name = Column(String(120), nullable=False)
    created_on = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_on = Column(TIMESTAMP, nullable=True)
