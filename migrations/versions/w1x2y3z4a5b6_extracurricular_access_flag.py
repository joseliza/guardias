"""Convierte rol extracurricular en flag extracurricular_access sobre teacher

Revision ID: w1x2y3z4a5b6
Revises: v1w2x3y4z5a6
Create Date: 2026-09-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'w1x2y3z4a5b6'
down_revision = 'v1w2x3y4z5a6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('extracurricular_access', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE users SET extracurricular_access = 1, role = 'teacher' WHERE role = 'extracurricular'")


def downgrade():
    op.execute("UPDATE users SET role = 'extracurricular' WHERE extracurricular_access = 1 AND role = 'teacher'")
    op.drop_column('users', 'extracurricular_access')
