from datetime import datetime
from sqlalchemy.orm import Session
from models.user import User


def get_user(db: Session, tg_id: int):
    return db.query(User).filter(User.tg_id == tg_id).first()


def user_exists(db: Session, tg_id: int):
    return db.query(db.query(User).filter(User.tg_id == tg_id).exists()).scalar()


def get_user_by_username(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()


def get_users(db: Session, skip: int = 0, limit: int = 100):
    return db.query(User).offset(skip).limit(limit).all()


def update_user(db: Session, tg_id, username, first_name, last_name):
    db_user = get_user(db, tg_id=tg_id)

    if db_user is not None:
        if username is not None:
            db_user.username = username
        if first_name is not None:
            db_user.first_name = first_name
        if last_name is not None:
            db_user.last_name = last_name
            
        db_user.last_login = datetime.now()

        db.commit()
        db.refresh(db_user)
        
    return db_user


def create_user(db: Session, tg_id, username, first_name, last_name):
    db_user = User(
        tg_id=tg_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        last_login=datetime.now()
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user
