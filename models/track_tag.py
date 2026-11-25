from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    Text,
)

from connectors.db import Base


class TrackTag(Base):
    """A tag link on one library track (WebApp v2 spec §4.1).

    `tg_id` repeats the track's owner on purpose: `track_key` alone is
    NOT unique across users (Spotify track ids are global), so the
    composite (tg_id, track_key) is what references the library row.
    Both links cascade — deleting the track row or the tag drops the
    membership (the spec's orphan pruning).
    """

    __tablename__ = "track_tags"

    tg_id = Column(Integer, nullable=False)
    track_key = Column(Text, nullable=False)
    tag_id = Column(BigInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("tg_id", "track_key", "tag_id"),
        ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tg_id", "track_key"],
            ["tracks.tg_id", "tracks.track_key"], ondelete="CASCADE"),
    )
