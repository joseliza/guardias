"""Hace zone_id nullable en recreo_assignments para representar 'Sin guardia'

Revision ID: z1a2b3c4d5e6
Revises: y1z2a3b4c5d6
Create Date: 2026-09-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'z1a2b3c4d5e6'
down_revision = 'y1z2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE recreo_assignments MODIFY COLUMN zone_id INT NULL")


def downgrade():
    # Borra primero los registros 'sin guardia' (zone_id NULL) para poder volver a NOT NULL
    op.execute("DELETE FROM recreo_assignments WHERE zone_id IS NULL")
    op.execute("ALTER TABLE recreo_assignments MODIFY COLUMN zone_id INT NOT NULL")
