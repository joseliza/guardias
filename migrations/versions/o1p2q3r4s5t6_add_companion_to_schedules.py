"""Añade companion_teacher_id a teacher_schedules (desdobles: a qué titular acompaña)

Revision ID: o1p2q3r4s5t6
Revises: n1o2p3q4r5s6
Create Date: 2026-06-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'o1p2q3r4s5t6'
down_revision = 'n1o2p3q4r5s6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('teacher_schedules',
                  sa.Column('companion_teacher_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_schedule_companion', 'teacher_schedules', 'users',
                          ['companion_teacher_id'], ['id'])


def downgrade():
    op.drop_constraint('fk_schedule_companion', 'teacher_schedules', type_='foreignkey')
    op.drop_column('teacher_schedules', 'companion_teacher_id')
