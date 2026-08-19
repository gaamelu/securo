"""add insights classification columns to categories and transactions

Revision ID: 071
Revises: 070
Create Date: 2026-08-18

Three additive columns, none of them backfilled here:

  - `categories.flow_type` — nullable string, one of 'income', 'consumption',
    'saving', 'transfer'. Describes what a category's money *does*.
  - `categories.expense_nature` — nullable string, one of 'fixed', 'variable',
    'discretionary'. The default classification for transactions in that
    category.
  - `transactions.expense_nature` — same value set as above, but per
    transaction: an override for the rare case where a transaction's nature
    differs from its category's default (e.g. an unusually large one-off
    purchase in an otherwise "fixed" category).

All three are nullable with no server default, and nothing reads them yet —
this migration only makes room for a later classification script to fill
them in. Indexes are intentionally omitted: `categories` holds ~32 rows, so
the planner will seq scan regardless of any index, and an index there is
pure write cost with no read benefit.

WARNING: once a later classification script has run and populated these
columns with manually-entered or manually-reviewed classification data,
downgrading below this migration will DESTROY that data — `downgrade()`
drops the columns outright, with no attempt to preserve their contents. It
is only safe to run `downgrade()` here before that script has ever run.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "072"
down_revision: Union[str, None] = "071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column("flow_type", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "categories",
        sa.Column("expense_nature", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("expense_nature", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transactions", "expense_nature")
    op.drop_column("categories", "expense_nature")
    op.drop_column("categories", "flow_type")
