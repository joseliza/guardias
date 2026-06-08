"""Añade google_drive_token y google_drive_file_id a users

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-06-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'f1a2b3c4d5e6'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = [c['name'] for c in inspector.get_columns('users')]
    if 'google_drive_token' not in existing:
        op.add_column('users', sa.Column('google_drive_token', sa.Text(), nullable=True))
    if 'google_drive_file_id' not in existing:
        op.add_column('users', sa.Column('google_drive_file_id', sa.String(200), nullable=True))


def downgrade():
    op.drop_column('users', 'google_drive_file_id')
    op.drop_column('users', 'google_drive_token')
