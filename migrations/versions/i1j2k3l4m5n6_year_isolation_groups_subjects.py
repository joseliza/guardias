"""Aísla grupos, materias, extraescolares y periodos de disponibilidad por curso escolar

Revision ID: i1j2k3l4m5n6
Revises: h1i2j3k4l5m6
Create Date: 2026-06-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'i1j2k3l4m5n6'
down_revision = 'h1i2j3k4l5m6'
branch_labels = None
depends_on = None


def _current_year_id(conn):
    row = conn.execute(sa.text("SELECT id FROM school_years WHERE is_current = 1 LIMIT 1")).fetchone()
    return row[0] if row else None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    year_id = _current_year_id(conn)

    # ── groups ──────────────────────────────────────────────────────────────────
    existing_g = [c['name'] for c in inspector.get_columns('groups')]
    if 'school_year_id' not in existing_g:
        op.add_column('groups', sa.Column('school_year_id', sa.Integer(),
                      sa.ForeignKey('school_years.id'), nullable=True))
        op.create_index('ix_groups_school_year_id', 'groups', ['school_year_id'])
    if year_id:
        conn.execute(sa.text(f"UPDATE `groups` SET school_year_id = {year_id} WHERE school_year_id IS NULL"))

    # ── subjects ────────────────────────────────────────────────────────────────
    existing_s = [c['name'] for c in inspector.get_columns('subjects')]
    if 'school_year_id' not in existing_s:
        op.add_column('subjects', sa.Column('school_year_id', sa.Integer(),
                      sa.ForeignKey('school_years.id'), nullable=True))
        op.create_index('ix_subjects_school_year_id', 'subjects', ['school_year_id'])
        # Eliminar unique global en name (ahora unique por curso)
        try:
            op.drop_constraint('subjects_name_key', 'subjects', type_='unique')
        except Exception:
            pass
        try:
            op.drop_index('uq_subjects_name', table_name='subjects')
        except Exception:
            pass
    if year_id:
        conn.execute(sa.text(f"UPDATE subjects SET school_year_id = {year_id} WHERE school_year_id IS NULL"))

    # ── extra_activities ─────────────────────────────────────────────────────────
    existing_ea = [c['name'] for c in inspector.get_columns('extra_activities')]
    if 'school_year_id' not in existing_ea:
        op.add_column('extra_activities', sa.Column('school_year_id', sa.Integer(),
                      sa.ForeignKey('school_years.id'), nullable=True))
        op.create_index('ix_extra_activities_school_year_id', 'extra_activities', ['school_year_id'])
    if year_id:
        conn.execute(sa.text(f"UPDATE extra_activities SET school_year_id = {year_id} WHERE school_year_id IS NULL"))

    # ── availability_periods ─────────────────────────────────────────────────────
    existing_ap = [c['name'] for c in inspector.get_columns('availability_periods')]
    if 'school_year_id' not in existing_ap:
        op.add_column('availability_periods', sa.Column('school_year_id', sa.Integer(),
                      sa.ForeignKey('school_years.id'), nullable=True))
        op.create_index('ix_availability_periods_school_year_id', 'availability_periods', ['school_year_id'])
    if year_id:
        conn.execute(sa.text(f"UPDATE availability_periods SET school_year_id = {year_id} WHERE school_year_id IS NULL"))


def downgrade():
    op.drop_index('ix_availability_periods_school_year_id', table_name='availability_periods')
    op.drop_column('availability_periods', 'school_year_id')
    op.drop_index('ix_extra_activities_school_year_id', table_name='extra_activities')
    op.drop_column('extra_activities', 'school_year_id')
    op.drop_index('ix_subjects_school_year_id', table_name='subjects')
    op.drop_column('subjects', 'school_year_id')
    op.drop_index('ix_groups_school_year_id', table_name='groups')
    op.drop_column('groups', 'school_year_id')
