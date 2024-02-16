from datetime import datetime
from sqlalchemy.orm import Session
from models.log import Log

def create_log(db: Session, tg_id, message):
    db_log = Log(
        tg_id=tg_id,
        message=message
    )
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log
