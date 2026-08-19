"""separate provider bill membership from user bill overrides

Revision ID: 074
Revises: 073
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


revision: str = "074"
down_revision: Union[str, None] = "073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # local/live previously applied this schema under revisions 071/072.
    # Those IDs now belong to public migrations, so tolerate the already
    # materialized columns, constraint, and index while advancing Alembic.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("transactions")}
    if "provider_bill_id" not in columns:
        op.add_column(
            "transactions",
            sa.Column(
                "provider_bill_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        )
        columns.add("provider_bill_id")

    foreign_key_name = "fk_transactions_provider_bill_id_credit_card_bills"
    foreign_keys = {
        foreign_key.get("name") for foreign_key in inspector.get_foreign_keys("transactions")
    }
    if foreign_key_name not in foreign_keys:
        op.create_foreign_key(
            foreign_key_name,
            "transactions",
            "credit_card_bills",
            ["provider_bill_id"],
            ["id"],
            ondelete="SET NULL",
        )

    index_name = "ix_transactions_provider_bill_id"
    indexes = {index.get("name") for index in inspector.get_indexes("transactions")}
    if index_name not in indexes:
        op.create_index(index_name, "transactions", ["provider_bill_id"])

    if "provider_bill_membership_known" not in columns:
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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("transactions")}
    if "provider_bill_membership_known" in columns:
        op.drop_column("transactions", "provider_bill_membership_known")
    indexes = {index.get("name") for index in inspector.get_indexes("transactions")}
    if "ix_transactions_provider_bill_id" in indexes:
        op.drop_index("ix_transactions_provider_bill_id", table_name="transactions")
    foreign_keys = {
        foreign_key.get("name") for foreign_key in inspector.get_foreign_keys("transactions")
    }
    if "fk_transactions_provider_bill_id_credit_card_bills" in foreign_keys:
        op.drop_constraint(
            "fk_transactions_provider_bill_id_credit_card_bills",
            "transactions",
            type_="foreignkey",
        )
    if "provider_bill_id" in columns:
        op.drop_column("transactions", "provider_bill_id")
