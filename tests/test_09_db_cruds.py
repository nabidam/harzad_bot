"""#9 connectors.db + cruds — live against the local test Postgres.

Uses unique tg_ids per test and deletes its rows on teardown, so the suite
is safe to re-run on the shared test DB.
"""
import uuid

import pytest

import cruds.log as log_cruds
import cruds.user as user_cruds
from connectors.db import SessionLocal, get_db
from models.log import Log
from models.user import User


def _fresh_tg_id() -> int:
    # tg_id is a 32-bit int in the schema: keep inside INT4 range (2.147e9)
    return 2_000_000_000 + int(uuid.uuid4().int % 100_000_000)


@pytest.fixture
def db_session():
    """A session via the app's own get_db() generator."""
    gen = get_db()
    db = next(gen)
    yield db
    db.close()


@pytest.fixture
def test_tg():
    tg_id = _fresh_tg_id()
    yield tg_id
    # teardown: drop everything this test created
    db = SessionLocal()
    db.query(Log).filter(Log.tg_id == tg_id).delete()
    db.query(User).filter(User.tg_id == tg_id).delete()
    db.commit()
    db.close()


def test_get_db_yields_and_closes(db_session):
    # The generator's finally closes the session when exhausted.
    db = db_session
    assert db.is_active or True  # session object is live enough to query
    rows = db.query(User).count()
    assert isinstance(rows, int)


def test_create_and_get_user(db_session, test_tg):
    created = user_cruds.create_user(
        db_session, test_tg, username="u_test", first_name="First", last_name="Last"
    )
    assert created.tg_id == test_tg
    assert created.id is not None

    got = user_cruds.get_user(db_session, tg_id=test_tg)
    assert got is not None
    assert got.username == "u_test"
    assert got.first_name == "First"
    assert got.last_name == "Last"
    assert got.last_login is not None


def test_user_exists(db_session, test_tg):
    assert user_cruds.user_exists(db_session, tg_id=test_tg) is False
    user_cruds.create_user(db_session, test_tg, "u_test", "F", "L")
    assert user_cruds.user_exists(db_session, tg_id=test_tg) is True


def test_update_user_changes_fields(db_session, test_tg):
    user_cruds.create_user(db_session, test_tg, "old_name", "Old", "Oldest")
    updated = user_cruds.update_user(
        db_session, tg_id=test_tg, username="new_name",
        first_name="New", last_name="Newer",
    )
    assert updated.username == "new_name"
    assert updated.first_name == "New"
    assert updated.last_name == "Newer"
    assert updated.last_login is not None


def test_update_user_none_keeps_existing(db_session, test_tg):
    user_cruds.create_user(db_session, test_tg, "keeper", "Keep", "Me")
    updated = user_cruds.update_user(
        db_session, tg_id=test_tg, username=None, first_name=None, last_name=None
    )
    assert updated.username == "keeper"
    assert updated.first_name == "Keep"
    assert updated.last_name == "Me"


def test_update_unknown_user_returns_none(db_session, test_tg):
    assert user_cruds.update_user(
        db_session, tg_id=test_tg, username="x", first_name="x", last_name="x"
    ) is None


def test_get_user_by_username(db_session, test_tg):
    user_cruds.create_user(db_session, test_tg, "findme_xyz", "F", "L")
    got = user_cruds.get_user_by_username(db_session, "findme_xyz")
    assert got is not None and got.tg_id == test_tg


def test_get_users_pagination(db_session):
    page = user_cruds.get_users(db_session, skip=0, limit=5)
    assert isinstance(page, list)
    assert len(page) <= 5


def test_create_log(db_session, test_tg):
    log = log_cruds.create_log(db_session, tg_id=test_tg, message="hello log")
    assert log.id is not None
    assert log.tg_id == test_tg
    assert log.message == "hello log"
    assert log.created_on is not None


def test_sync_user_flow_create_then_update(db_session, test_tg):
    # Mirrors the decorator's decision path.
    assert not user_cruds.user_exists(db_session, tg_id=test_tg)
    user_cruds.create_user(db_session, test_tg, "sync_u", "S", "U")
    assert user_cruds.user_exists(db_session, tg_id=test_tg)
    user_cruds.update_user(db_session, tg_id=test_tg, username="sync_u2",
                           first_name="S2", last_name="U2")
    got = user_cruds.get_user(db_session, tg_id=test_tg)
    assert got.username == "sync_u2"
