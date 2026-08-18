"""separate provider bill membership from user bill overrides

Revision ID: 071
Revises: 070
Create Date: 2026-08-18

`transactions.bill_id` is user-facing state and may be moved by an explicit
cycle override. Persisting the provider-observed membership separately keeps
reconciliation deterministic without weakening user authority. The companion
boolean distinguishes an authoritative provider null from membership that has
not been observed yet.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "071"
down_revision: Union[str, None] = "070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "provider_bill_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_transactions_provider_bill_id_credit_card_bills",
        "transactions",
        "credit_card_bills",
        ["provider_bill_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_transactions_provider_bill_id",
        "transactions",
        ["provider_bill_id"],
    )
    op.add_column(
        "transactions",
        sa.Column(
            "provider_bill_membership_known",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        UPDATE transactions
        SET provider_bill_id = bill_id,
            provider_bill_membership_known = true
        WHERE source = 'sync'
          AND bill_id IS NOT NULL
          AND effective_bill_date IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("transactions", "provider_bill_membership_known")
    op.drop_index("ix_transactions_provider_bill_id", table_name="transactions")
    op.drop_constraint(
        "fk_transactions_provider_bill_id_credit_card_bills",
        "transactions",
        type_="foreignkey",
    )
    op.drop_column("transactions", "provider_bill_id")
