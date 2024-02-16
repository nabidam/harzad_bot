from sqlalchemy import Column, String, TIMESTAMP, text, JSON, DATETIME, Integer, TEXT
from sqlalchemy.dialects.mysql import INTEGER

from connectors.db import Base

class Log(Base):
    __tablename__ = 'logs'

    id = Column(INTEGER(11), primary_key=True)
    tg_id = Column(Integer)
    message = Column(TEXT)
    created_on = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))