"""Añade show_substitute_public a users para controlar visibilidad en dashboard/display

Revision ID: k1l2m3n4o5p6
Revises: j1k2l3m4n5o6
Create Date: 2026-06-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'k1l2m3n4o5p6'
down_revision = 'j1k2l3m4n5o6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = [c['name'] for c in inspector.get_columns('users')]
    if 'show_substitute_public' not in existing:
        op.add_column('users', sa.Column('show_substitute_public', sa.Boolean(),
                      nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column('users', 'show_substitute_public')
