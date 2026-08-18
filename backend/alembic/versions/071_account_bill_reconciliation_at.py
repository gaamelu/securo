"""track successful bill reconciliation per account

Revision ID: 071
Revises: 070
Create Date: 2026-08-18

An incremental fallback can complete even when a provider's full credit-card
bill snapshot fails. A per-account timestamp ensures that success does not
consume the account's reconciliation attempt, nor suppress another card on
the same connection.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "071"
down_revision: Union[str, None] = "070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "last_bill_reconciliation_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("accounts", "last_bill_reconciliation_at")
