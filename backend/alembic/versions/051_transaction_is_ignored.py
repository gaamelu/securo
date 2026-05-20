"""add is_ignored to transactions

Revision ID: 051
Revises: 050
Create Date: 2026-05-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '051'
down_revision: Union[str, Sequence[str], None] = '050'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'transactions',
        sa.Column('is_ignored', sa.Boolean(), nullable=False, server_default='false')
    )
    op.create_index('ix_transactions_is_ignored', 'transactions', ['is_ignored'])


def downgrade() -> None:
    op.drop_index('ix_transactions_is_ignored', table_name='transactions')
    op.drop_column('transactions', 'is_ignored')
