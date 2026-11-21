"""Add Aadhaar lookup and account approval

Revision ID: 9c1d9cc3ad40
Revises: 0c4caf7350a8
Create Date: 2026-11-21 21:48:51.101949

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c1d9cc3ad40'
down_revision = '0c4caf7350a8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('aadhaar_digest', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('aadhaar_last4', sa.String(length=4), nullable=True))
        batch_op.add_column(sa.Column('approval_status', sa.String(length=16), nullable=True))
        batch_op.create_unique_constraint(batch_op.f('uq_user_aadhaar_digest'), ['aadhaar_digest'])

    op.execute(
        sa.text('UPDATE "user" SET approval_status = :status WHERE approval_status IS NULL')
        .bindparams(status="approved")
    )
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column(
            'approval_status',
            existing_type=sa.String(length=16),
            nullable=False,
        )


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_user_aadhaar_digest'), type_='unique')
        batch_op.drop_column('approval_status')
        batch_op.drop_column('aadhaar_last4')
        batch_op.drop_column('aadhaar_digest')
