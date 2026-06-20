"""Permite múltiples grupos por tramo en teacher_schedules (elimina unique constraint)

Revision ID: v1w2x3y4z5a6
Revises: t1u2v3w4x5y6
Create Date: 2026-06-20 00:00:00.000000

"""
from alembic import op

revision = 'v1w2x3y4z5a6'
down_revision = 't1u2v3w4x5y6'
branch_labels = None
depends_on = None


def upgrade():
    # MySQL requiere un índice en teacher_id para la FK → users antes de eliminar uq_schedule
    op.create_index('ix_ts_teacher_id', 'teacher_schedules', ['teacher_id'])
    op.drop_constraint('uq_schedule', 'teacher_schedules', type_='unique')


def downgrade():
    op.create_unique_constraint(
        'uq_schedule',
        'teacher_schedules',
        ['teacher_id', 'day_of_week', 'slot_id', 'school_year_id'],
    )
    op.drop_index('ix_ts_teacher_id', 'teacher_schedules')
