"""track successful bill reconciliation per account

Revision ID: 073
Revises: 072
Create Date: 2026-08-18

An incremental fallback can complete even when a provider's full credit-card
bill snapshot fails. A per-account timestamp ensures that success does not
consume the account's reconciliation attempt, nor suppress another card on
the same connection.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "073"
down_revision: Union[str, None] = "072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # local/live previously applied this schema under revision 071. The
    # public 071/072 migrations later occupied those IDs, so this branch
    # re-chains the same schema as 073. Keep upgrade compatible with that
    # existing database instead of attempting to add the column twice.
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("accounts")}
    if "last_bill_reconciliation_at" not in columns:
        op.add_column(
            "accounts",
            sa.Column(
                "last_bill_reconciliation_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("accounts")}
    if "last_bill_reconciliation_at" in columns:
        op.drop_column("accounts", "last_bill_reconciliation_at")
