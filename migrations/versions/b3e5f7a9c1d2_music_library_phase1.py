"""music library phase 1: tags, track tags, playlist name case-folding

Revision ID: b3e5f7a9c1d2
Revises: 8a3856139de3
Create Date: 2026-11-25 18:00:00.000000

WebApp v2 spec §4.1. The three v1-era music tables (tracks / playlists /
playlist_items) never had alembic migrations — this one creates them on a
fresh database (mirroring models/, as scripts/ci_schema.py's create_all
does for tests) and adds the Phase 1 surface on top:

- tags(tg_id, name), unique per user case-insensitively
- track_tags(tg_id, track_key, tag_id) — PK on all three; both links
  cascade (deleting the track row or the tag prunes the membership)
- playlists: case-insensitive name uniqueness replacing the
  case-sensitive unique constraint (the prod table is empty at
  migration time, verified 2026-11-25), plus an updated_on timestamp

Case-insensitive uniqueness is a UNIQUE INDEX on (tg_id, lower(name)):
a unique CONSTRAINT cannot carry the expression under SQLAlchemy 2.1's
declarative ("Can't add unnamed column to column collection"), and the
index enforces the identical rule.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b3e5f7a9c1d2'
down_revision: Union[str, None] = '8a3856139de3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_PLAYLIST_IDX = 'uq_playlists_tg_lower_name'
NEW_TAG_IDX = 'uq_tags_tg_lower_name'
OLD_PLAYLIST_UQ = 'playlists_tg_id_name_key'


def _insp():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    insp = _insp()

    # -- the v1-era tables, for a fresh install -----------------------------
    if not insp.has_table('tracks'):
        op.create_table('tracks',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tg_id', sa.Integer(), nullable=False),
        sa.Column('track_key', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('artist', sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column('album', sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column('duration_sec', sa.Integer(), nullable=True),
        sa.Column('cover_url', sa.Text(), nullable=True),
        sa.Column('spotify_url', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=32), nullable=False, server_default=sa.text("'spotify'")),
        sa.Column('file_id', sa.Text(), nullable=True),
        sa.Column('orig_chat_id', sa.BigInteger(), nullable=True),
        sa.Column('orig_message_id', sa.BigInteger(), nullable=True),
        sa.Column('request_count', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('added_on', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('last_requested', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tg_id', 'track_key', name='tracks_tg_id_track_key_key'),
        )

    if not insp.has_table('playlists'):
        op.create_table('playlists',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tg_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('created_on', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_on', sa.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        )

    if not insp.has_table('playlist_items'):
        op.create_table('playlist_items',
        sa.Column('playlist_id', sa.BigInteger(), nullable=False),
        sa.Column('track_id', sa.BigInteger(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('added_on', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('playlist_id', 'track_id'),
        sa.ForeignKeyConstraint(['playlist_id'], ['playlists.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
        )

    # -- Phase 1: tags -------------------------------------------------------
    if not insp.has_table('tags'):
        op.create_table('tags',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tg_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('created_on', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(NEW_TAG_IDX, 'tags',
                        ['tg_id', sa.text('lower(name)')], unique=True)

    if not insp.has_table('track_tags'):
        op.create_table('track_tags',
        sa.Column('tg_id', sa.Integer(), nullable=False),
        sa.Column('track_key', sa.Text(), nullable=False),
        sa.Column('tag_id', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('tg_id', 'track_key', 'tag_id'),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tg_id', 'track_key'], ['tracks.tg_id', 'tracks.track_key'],
                                ondelete='CASCADE'),
        )

    # -- playlists: updated_on + case-insensitive name ------------------------
    insp = _insp()
    cols = [c['name'] for c in insp.get_columns('playlists')]
    if 'updated_on' not in cols:
        op.add_column('playlists',
                      sa.Column('updated_on', sa.TIMESTAMP(), nullable=True))

    insp = _insp()
    uqs = {u['name'] for u in insp.get_unique_constraints('playlists')}
    if OLD_PLAYLIST_UQ in uqs:
        op.drop_constraint(OLD_PLAYLIST_UQ, 'playlists', type_='unique')
    idx_names = {i['name'] for i in insp.get_indexes('playlists')}
    if NEW_PLAYLIST_IDX not in idx_names:
        op.create_index(NEW_PLAYLIST_IDX, 'playlists',
                        ['tg_id', sa.text('lower(name)')], unique=True)


def downgrade() -> None:
    insp = _insp()
    if insp.has_table('track_tags'):
        op.drop_table('track_tags')
    if insp.has_table('tags'):
        op.drop_index(NEW_TAG_IDX, table_name='tags')
        op.drop_table('tags')

    insp = _insp()
    if NEW_PLAYLIST_IDX in {i['name'] for i in insp.get_indexes('playlists')}:
        op.drop_index(NEW_PLAYLIST_IDX, table_name='playlists')
        op.create_unique_constraint(OLD_PLAYLIST_UQ, 'playlists',
                                    ['tg_id', 'name'])
    if 'updated_on' in [c['name'] for c in insp.get_columns('playlists')]:
        op.drop_column('playlists', 'updated_on')
    # the v1-era tables are dropped by nothing: they predate this
    # migration's authority (see module docstring)
