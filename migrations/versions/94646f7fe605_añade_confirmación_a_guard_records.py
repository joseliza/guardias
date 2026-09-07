"""Añade confirmación a guard_records

Revision ID: 94646f7fe605
Revises: z1a2b3c4d5e6
Create Date: 2026-09-07 18:28:02.908602

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '94646f7fe605'
down_revision = 'z1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    # El profesor debe confirmar pulsando su nombre al llegar el tramo para
    # que la guardia cuente puntos; hasta entonces confirmed=False. Se pone
    # server_default para las filas ya existentes y se retira después, para
    # que el modelo (default en Python, no en la columna) mande a partir de aquí.
    with op.batch_alter_table('guard_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('confirmed', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('confirmed_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('guard_records', schema=None) as batch_op:
        batch_op.alter_column('confirmed', server_default=None)


def downgrade():
    with op.batch_alter_table('guard_records', schema=None) as batch_op:
        batch_op.drop_column('confirmed_at')
        batch_op.drop_column('confirmed')
