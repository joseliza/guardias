"""Añade tabla school_years y school_year_id a teacher_schedules

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-06-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e1f2a3b4c5d6'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Crear school_years si no existe
    if 'school_years' not in inspector.get_table_names():
        op.create_table(
            'school_years',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(20), nullable=False),
            sa.Column('start_date', sa.Date(), nullable=False),
            sa.Column('end_date', sa.Date(), nullable=False),
            sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )

    # 2. Añadir school_year_id a teacher_schedules si no existe
    existing_cols = [c['name'] for c in inspector.get_columns('teacher_schedules')]
    if 'school_year_id' not in existing_cols:
        op.add_column('teacher_schedules',
            sa.Column('school_year_id', sa.Integer(),
                      sa.ForeignKey('school_years.id'), nullable=True))

    # 3. Insertar el curso escolar actual si la tabla está vacía
    count = conn.execute(sa.text("SELECT COUNT(*) FROM school_years")).scalar()
    if count == 0:
        from datetime import date
        today = date.today()
        start_year = today.year if today.month >= 9 else today.year - 1
        year_name = f"{start_year}/{start_year + 1}"
        start_date = f"{start_year}-09-01"
        end_date = f"{start_year + 1}-06-30"
        conn.execute(sa.text(
            "INSERT INTO school_years (name, start_date, end_date, is_current) "
            f"VALUES ('{year_name}', '{start_date}', '{end_date}', TRUE)"
        ))

    # 4. Asignar el curso activo a todos los tramos sin año
    conn.execute(sa.text(
        "UPDATE teacher_schedules SET school_year_id = "
        "(SELECT id FROM school_years WHERE is_current = TRUE) "
        "WHERE school_year_id IS NULL"
    ))

    # 5. Hacer NOT NULL con SQL nativo (MySQL no acepta alter_column sin tipo)
    conn.execute(sa.text(
        "ALTER TABLE teacher_schedules MODIFY school_year_id INT NOT NULL"
    ))

    # 6. Actualizar la restricción única para incluir school_year_id
    # MySQL no permite DROP + ADD separados cuando el índice respalda una FK:
    # hay que hacerlo en una sola sentencia ALTER TABLE.
    existing_uqs = {u['name']: u['column_names'] for u in inspector.get_unique_constraints('teacher_schedules')}
    if 'uq_schedule' in existing_uqs:
        if 'school_year_id' not in existing_uqs['uq_schedule']:
            conn.execute(sa.text(
                "ALTER TABLE teacher_schedules "
                "DROP INDEX uq_schedule, "
                "ADD UNIQUE KEY uq_schedule (teacher_id, day_of_week, slot_id, school_year_id)"
            ))
    else:
        conn.execute(sa.text(
            "ALTER TABLE teacher_schedules "
            "ADD UNIQUE KEY uq_schedule (teacher_id, day_of_week, slot_id, school_year_id)"
        ))


def downgrade():
    op.drop_constraint('uq_schedule', 'teacher_schedules', type_='unique')
    op.create_unique_constraint(
        'uq_schedule', 'teacher_schedules',
        ['teacher_id', 'day_of_week', 'slot_id']
    )
    op.drop_column('teacher_schedules', 'school_year_id')
    op.drop_table('school_years')
