"""Add chat_clears table

Revision ID: b7d4f1a3c82e
Revises: e2225990bcf0
Create Date: 2026-05-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7d4f1a3c82e'
down_revision = 'e2225990bcf0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('chat_clears',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cleared_at', sa.DateTime(), nullable=False),
        sa.Column('cleared_by_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['cleared_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('chat_clears')
