"""Simplifica desdobles: elimina companion_teacher_id y amplía guard_type (Desdoble FP)

Revision ID: p1q2r3s4t5u6
Revises: o1p2q3r4s5t6
Create Date: 2026-06-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'p1q2r3s4t5u6'
down_revision = 'o1p2q3r4s5t6'
branch_labels = None
depends_on = None


def upgrade():
    # 'desdoble_fp' supera los 10 caracteres
    op.alter_column('subjects', 'guard_type',
                    existing_type=sa.String(length=10),
                    type_=sa.String(length=20),
                    existing_nullable=True)
    op.drop_constraint('fk_schedule_companion', 'teacher_schedules', type_='foreignkey')
    op.drop_column('teacher_schedules', 'companion_teacher_id')


def downgrade():
    op.add_column('teacher_schedules',
                  sa.Column('companion_teacher_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_schedule_companion', 'teacher_schedules', 'users',
                          ['companion_teacher_id'], ['id'])
    op.alter_column('subjects', 'guard_type',
                    existing_type=sa.String(length=20),
                    type_=sa.String(length=10),
                    existing_nullable=True)
