"""Las guardias pasan de tipo de materia a tipo de grupo.

Añade groups.guard_type, lo rellena detectando las abreviaturas del centro
(GUARD → 'guard', GUA-2/GUA2 → 'guard_55'), marca como tramo de guardia los
horarios cuyo grupo es de tipo 'guard' y limpia los tipos de guardia que
quedaban en subjects (solo se conserva 'desdoble_fp').

Revision ID: r1s2t3u4v5w6
Revises: q1r2s3t4u5v6
Create Date: 2026-06-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'r1s2t3u4v5w6'
down_revision = 'q1r2s3t4u5v6'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # Idempotente: en MySQL el DDL no es transaccional y un fallo posterior
    # de esta misma migración puede dejar la columna ya creada.
    cols = {c["name"] for c in sa.inspect(bind).get_columns("groups")}
    if "guard_type" not in cols:
        op.add_column('groups', sa.Column('guard_type', sa.String(length=20), nullable=True))

    # `groups` es palabra reservada en MySQL 8: hay que escaparla.
    bind.execute(sa.text(
        "UPDATE `groups` SET guard_type='guard' WHERE UPPER(TRIM(abbreviation))='GUARD'"
    ))
    bind.execute(sa.text(
        "UPDATE `groups` SET guard_type='guard_55' "
        "WHERE UPPER(TRIM(abbreviation)) IN ('GUA-2', 'GUA2')"
    ))
    # Horarios con grupo de guardia oficial → tramo de guardia (pool primario)
    bind.execute(sa.text(
        "UPDATE teacher_schedules ts JOIN `groups` g ON ts.group_id = g.id "
        "SET ts.is_guard_slot = 1 WHERE g.guard_type = 'guard'"
    ))
    # Las materias ya no llevan tipo de guardia (solo queda 'desdoble_fp')
    bind.execute(sa.text(
        "UPDATE subjects SET guard_type=NULL WHERE guard_type IN ('guard', 'guard_55')"
    ))


def downgrade():
    op.drop_column('groups', 'guard_type')
