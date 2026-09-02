"""Cambia week_start por assignment_date en recreo_assignments (asignación diaria)

Revision ID: y1z2a3b4c5d6
Revises: x1y2z3a4b5c6
Create Date: 2026-09-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'y1z2a3b4c5d6'
down_revision = 'x1y2z3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE recreo_assignments CHANGE COLUMN week_start assignment_date DATE NOT NULL")
    op.execute("ALTER TABLE recreo_assignments ADD UNIQUE KEY uq_recreo_day_zone (assignment_date, zone_id)")
    op.execute("ALTER TABLE recreo_assignments ADD UNIQUE KEY uq_recreo_day_teacher (assignment_date, teacher_id)")


def downgrade():
    op.execute("ALTER TABLE recreo_assignments DROP INDEX uq_recreo_day_zone")
    op.execute("ALTER TABLE recreo_assignments DROP INDEX uq_recreo_day_teacher")
    op.execute("ALTER TABLE recreo_assignments CHANGE COLUMN assignment_date week_start DATE NOT NULL")
