from sqlalchemy import BigInteger, Column, ForeignKey, Integer, TIMESTAMP, text

from connectors.db import Base


class PlaylistItem(Base):
    """Position of a track inside a playlist (music-library plan §4).

    Deleting the playlist or the track row cascades (ON DELETE CASCADE).
    """

    __tablename__ = "playlist_items"

    playlist_id = Column(
        BigInteger,
        ForeignKey("playlists.id", ondelete="CASCADE"),
        primary_key=True,
    )
    track_id = Column(
        BigInteger,
        ForeignKey("tracks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position = Column(Integer, nullable=False)
    added_on = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
