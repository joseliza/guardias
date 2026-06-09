"""Añade school_year_id a users para asociar cada profesor a su curso escolar

Revision ID: j1k2l3m4n5o6
Revises: i1j2k3l4m5n6
Create Date: 2026-06-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'j1k2l3m4n5o6'
down_revision = 'i1j2k3l4m5n6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = [c['name'] for c in inspector.get_columns('users')]
    if 'school_year_id' not in existing:
        op.add_column('users', sa.Column('school_year_id', sa.Integer(),
                      sa.ForeignKey('school_years.id'), nullable=True))
        op.create_index('ix_users_school_year_id', 'users', ['school_year_id'])


def downgrade():
    op.drop_index('ix_users_school_year_id', table_name='users')
    op.drop_column('users', 'school_year_id')
