"""Add user_presence table

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-06-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'b3c4d5e6f7a8'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_presence',
        sa.Column('user_id',   sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('last_seen', sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table('user_presence')
