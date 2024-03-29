"""log

Revision ID: 8a3856139de3
Revises: df8b87b225dc
Create Date: 2024-02-16 11:43:50.671832

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '8a3856139de3'
down_revision: Union[str, None] = 'df8b87b225dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('logs',
    sa.Column('id', mysql.INTEGER(display_width=11), nullable=False),
    sa.Column('tg_id', sa.Integer(), nullable=True),
    sa.Column('message', sa.TEXT(), nullable=True),
    sa.Column('created_on', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('logs')
    # ### end Alembic commands ###
