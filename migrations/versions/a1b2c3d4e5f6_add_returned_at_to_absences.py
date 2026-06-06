"""Add returned_at to absences

Revision ID: a1b2c3d4e5f6
Revises: 50cf675c3151
Create Date: 2026-06-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = '7b82e7cf6367'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('absences', sa.Column('returned_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('absences', 'returned_at')
