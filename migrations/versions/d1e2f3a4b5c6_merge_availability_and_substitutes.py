"""Merge availability_periods y substitutes_to_users

Revision ID: d1e2f3a4b5c6
Revises: 8e64f3e972e2, c1d2e3f4a5b6
Create Date: 2026-06-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'd1e2f3a4b5c6'
down_revision = ('8e64f3e972e2', 'c1d2e3f4a5b6')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
