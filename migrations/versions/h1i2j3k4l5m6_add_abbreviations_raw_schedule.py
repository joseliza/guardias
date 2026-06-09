"""Añade abbreviation a users y groups, description a rooms, tabla raw_schedule_rows

Revision ID: h1i2j3k4l5m6
Revises: g1h2i3j4k5l6
Create Date: 2026-06-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'h1i2j3k4l5m6'
down_revision = 'g1h2i3j4k5l6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_users = [c['name'] for c in inspector.get_columns('users')]
    if 'abbreviation' not in existing_users:
        op.add_column('users', sa.Column('abbreviation', sa.String(20), nullable=True))
        op.create_index('ix_users_abbreviation', 'users', ['abbreviation'])

    existing_groups = [c['name'] for c in inspector.get_columns('groups')]
    if 'abbreviation' not in existing_groups:
        op.add_column('groups', sa.Column('abbreviation', sa.String(20), nullable=True))
        op.create_index('ix_groups_abbreviation', 'groups', ['abbreviation'])

    existing_rooms = [c['name'] for c in inspector.get_columns('rooms')]
    if 'description' not in existing_rooms:
        op.add_column('rooms', sa.Column('description', sa.String(200), nullable=True))

    if 'raw_schedule_rows' not in inspector.get_table_names():
        op.create_table(
            'raw_schedule_rows',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('school_year_id', sa.Integer(), sa.ForeignKey('school_years.id'), nullable=False),
            sa.Column('teacher_abbr', sa.String(20), nullable=False),
            sa.Column('subject_abbr', sa.String(20), nullable=True),
            sa.Column('group_abbr', sa.String(20), nullable=False),
            sa.Column('room_abbr', sa.String(20), nullable=True),
            sa.Column('day_of_week', sa.Integer(), nullable=False),
            sa.Column('slot_number', sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade():
    op.drop_table('raw_schedule_rows')
    op.drop_index('ix_groups_abbreviation', table_name='groups')
    op.drop_column('groups', 'abbreviation')
    op.drop_index('ix_users_abbreviation', table_name='users')
    op.drop_column('users', 'abbreviation')
    op.drop_column('rooms', 'description')
