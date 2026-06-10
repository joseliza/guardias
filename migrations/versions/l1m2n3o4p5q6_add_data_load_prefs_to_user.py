"""Añade data_load_prefs a users

Revision ID: l1m2n3o4p5q6
Revises: k1l2m3n4o5p6
Create Date: 2026-06-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'l1m2n3o4p5q6'
down_revision = 'k1l2m3n4o5p6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = [c['name'] for c in inspector.get_columns('users')]
    if 'data_load_prefs' not in existing:
        op.add_column('users', sa.Column('data_load_prefs', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('users', 'data_load_prefs')
