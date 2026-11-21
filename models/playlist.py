from sqlalchemy import BigInteger, Column, Integer, String, TIMESTAMP, UniqueConstraint, text

from connectors.db import Base


class Playlist(Base):
    """A named user collection of library tracks (music-library plan §4)."""

    __tablename__ = "playlists"
    __table_args__ = (UniqueConstraint("tg_id", "name"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tg_id = Column(Integer, nullable=False)
    name = Column(String(120), nullable=False)
    created_on = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
