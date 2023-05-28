from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, String, TIMESTAMP, text, JSON, DATETIME
from sqlalchemy.dialects.mysql import INTEGER

Base = declarative_base()
metadata = Base.metadata

class User(Base):

    __tablename__ = 'users'

    id = Column(INTEGER(11), primary_key=True)
    tg_id = Column(String(255))
    username = Column(String(255))
    fist_name = Column(String(255))
    last_name = Column(String(255))
    details = Column(JSON)
    last_login = Column(DATETIME)
    created_on = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))