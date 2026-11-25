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


class Tag(Base):
    """A user-defined label for library tracks (WebApp v2 spec §4.1).

    User-scoped; the name is unique per user case-insensitively —
    enforced as a unique index on (tg_id, lower(name)) ("Rock" and
    "rock" are one tag). A unique CONSTRAINT cannot carry the
    lower(name) expression under SQLAlchemy 2.1's declarative
    (`Can't add unnamed column`), but the index enforces the same
    rule."""

    __tablename__ = "tags"
    __table_args__ = (
        Index("uq_tags_tg_lower_name", "tg_id", text("lower(name)"),
              unique=True),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tg_id = Column(Integer, nullable=False)
    name = Column(String(80), nullable=False)
    created_on = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
