from sqlalchemy import Column, String, TIMESTAMP, text, JSON, DATETIME
from sqlalchemy.dialects.mysql import INTEGER

from connectors.db import Base

class User(Base):
    __tablename__ = 'users'

    id = Column(INTEGER(11), primary_key=True)
    tg_id = Column(String(255))
    username = Column(String(255))
    fist_name = Column(String(255))
    last_name = Column(String(255))
    details = Column(JSON)
    last_login = Column(TIMESTAMP, nullable=True)
    created_on = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))