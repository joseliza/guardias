"""Añade guard_type a subjects: GUARD (guardia oficial) y GUA-2 (guardia +55, solo pool de libres)

Revision ID: n1o2p3q4r5s6
Revises: m1n2o3p4q5r6
Create Date: 2026-06-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'n1o2p3q4r5s6'
down_revision = 'm1n2o3p4q5r6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('subjects', sa.Column('guard_type', sa.String(length=10), nullable=True))
    conn = op.get_bind()
    # Autodetectar por abreviatura las materias de guardia ya existentes
    conn.execute(sa.text(
        "UPDATE subjects SET guard_type='guard' WHERE UPPER(abbreviation)='GUARD'"
    ))
    conn.execute(sa.text(
        "UPDATE subjects SET guard_type='guard_55' WHERE UPPER(abbreviation) IN ('GUA-2','GUA2')"
    ))
    # Normalizar horarios ya importados con esas materias:
    # GUARD pasa a tramo de guardia oficial; GUA-2 queda como tramo normal (no guardia)
    conn.execute(sa.text(
        "UPDATE teacher_schedules ts JOIN subjects s ON ts.subject_id = s.id "
        "SET ts.is_guard_slot = 1 WHERE s.guard_type = 'guard'"
    ))
    conn.execute(sa.text(
        "UPDATE teacher_schedules ts JOIN subjects s ON ts.subject_id = s.id "
        "SET ts.is_guard_slot = 0 WHERE s.guard_type = 'guard_55'"
    ))


def downgrade():
    op.drop_column('subjects', 'guard_type')
