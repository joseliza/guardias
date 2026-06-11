"""Unifica el tipo de desdoble: 'desdoble' pasa a 'desdoble_fp'

Revision ID: q1r2s3t4u5v6
Revises: p1q2r3s4t5u6
Create Date: 2026-06-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'q1r2s3t4u5v6'
down_revision = 'p1q2r3s4t5u6'
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().execute(sa.text(
        "UPDATE subjects SET guard_type='desdoble_fp' WHERE guard_type='desdoble'"
    ))


def downgrade():
    pass
