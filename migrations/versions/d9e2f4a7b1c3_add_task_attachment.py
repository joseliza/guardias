"""Add attachment column to tasks

Revision ID: d9e2f4a7b1c3
Revises: b7d4f1a3c82e
Create Date: 2026-05-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd9e2f4a7b1c3'
down_revision = 'b7d4f1a3c82e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('attachment', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('attachment')
