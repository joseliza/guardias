"""Añade tabla subjects y subject_id a teacher_schedules

Revision ID: g1h2i3j4k5l6
Revises: f1a2b3c4d5e6
Create Date: 2026-06-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'g1h2i3j4k5l6'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'subjects' not in inspector.get_table_names():
        op.create_table(
            'subjects',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(100), nullable=False),
            sa.Column('abbreviation', sa.String(20), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )
        op.create_index('ix_subjects_abbreviation', 'subjects', ['abbreviation'])

    existing = [c['name'] for c in inspector.get_columns('teacher_schedules')]
    if 'subject_id' not in existing:
        op.add_column('teacher_schedules',
            sa.Column('subject_id', sa.Integer(),
                      sa.ForeignKey('subjects.id'), nullable=True))


def downgrade():
    op.drop_column('teacher_schedules', 'subject_id')
    op.drop_index('ix_subjects_abbreviation', table_name='subjects')
    op.drop_table('subjects')
