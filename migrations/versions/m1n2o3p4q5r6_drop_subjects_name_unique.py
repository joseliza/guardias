"""Elimina la restricción unique 'name' de subjects (permite nombres repetidos con distinta abreviatura)

Revision ID: m1n2o3p4q5r6
Revises: l1m2n3o4p5q6
Create Date: 2026-06-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'm1n2o3p4q5r6'
down_revision = 'l1m2n3o4p5q6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for uc in inspector.get_unique_constraints('subjects'):
        if uc['column_names'] == ['name']:
            op.drop_constraint(uc['name'], 'subjects', type_='unique')


def downgrade():
    op.create_unique_constraint('name', 'subjects', ['name'])
