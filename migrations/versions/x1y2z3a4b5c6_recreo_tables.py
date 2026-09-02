"""Añade tablas recreo_zones y recreo_assignments para guardias de recreo

Revision ID: x1y2z3a4b5c6
Revises: w1x2y3z4a5b6
Create Date: 2026-09-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'x1y2z3a4b5c6'
down_revision = 'w1x2y3z4a5b6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'recreo_zones',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('map_x', sa.Float(), nullable=True),
        sa.Column('map_y', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'recreo_assignments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('week_start', sa.Date(), nullable=False),
        sa.Column('zone_id', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=False),
        sa.Column('school_year_id', sa.Integer(), nullable=False),
        sa.Column('is_manual', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['zone_id'], ['recreo_zones.id']),
        sa.ForeignKeyConstraint(['teacher_id'], ['users.id']),
        sa.ForeignKeyConstraint(['school_year_id'], ['school_years.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('week_start', 'zone_id', name='uq_recreo_week_zone'),
        sa.UniqueConstraint('week_start', 'teacher_id', name='uq_recreo_week_teacher'),
    )


def downgrade():
    op.drop_table('recreo_assignments')
    op.drop_table('recreo_zones')
