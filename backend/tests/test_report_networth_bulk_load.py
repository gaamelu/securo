"""Parity tests for the net worth report's bulk-loaded balance path.

``get_net_worth_report`` walks a trend of date points and needs an account
balance at each one. Asking the database per account per point is correct but
quadratic, so the balances are preloaded once and resolved in Python instead.
That trades one source of truth for two, and the copy is only useful while it
agrees with the original at every cutoff.

These tests pin that agreement. The parity cases sweep a cutoff across a
seeded ledger and assert the preloaded answer equals ``_account_balance_at``
for the same account and date, across the cases that distinguish the two
implementations: pending rows, ignored transactions, ignored categories,
credit cards, connected versus manual accounts, and foreign-currency rows.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.transaction import Transaction
from app.services.dashboard_service import _account_balance_at
from app.services.report_service import (
    _asset_value_from_preloaded,
    _balance_from_preloaded,
    _bulk_load_account_balance_data,
    _bulk_load_asset_values,
    get_net_worth_report,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


async def _make_connection(session: AsyncSession, user_id: uuid.UUID) -> BankConnection:
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user_id, provider="test",
        external_id=f"ext-{uuid.uuid4()}", institution_name="Test Bank",
        credentials={}, status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()
    return conn


async def _make_account(
    session: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    *,
    acct_type: str = "checking",
    balance: str = "0",
    currency: str = "BRL",
    connection_id: uuid.UUID | None = None,
) -> Account:
    acct = Account(
        id=uuid.uuid4(), user_id=user_id, name=name, type=acct_type,
        balance=Decimal(balance), currency=currency,
        connection_id=connection_id, is_closed=False,
    )
    session.add(acct)
    await session.commit()
    await session.refresh(acct)
    return acct


async def _add_txn(
    session: AsyncSession,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    amount: str,
    txn_type: str,
    txn_date: date,
    *,
    status: str = "posted",
    currency: str = "BRL",
    amount_primary: str | None = None,
    is_ignored: bool = False,
    category_id: uuid.UUID | None = None,
    source: str = "manual",
) -> Transaction:
    txn = Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account_id,
        description=f"{txn_type} {amount}", amount=Decimal(amount),
        amount_primary=Decimal(amount_primary) if amount_primary is not None else None,
        date=txn_date, type=txn_type, source=source, currency=currency,
        status=status, is_ignored=is_ignored, category_id=category_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()
    return txn


async def _make_category(
    session: AsyncSession,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
    *,
    is_ignored: bool = False,
) -> Category:
    cat = Category(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        name=name, is_ignored=is_ignored,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


async def _assert_parity(session: AsyncSession, accounts: list[Account], cutoffs: list[date]):
    """The preloaded balance must equal the per-account query at every cutoff."""
    preloaded = await _bulk_load_account_balance_data(session, accounts)
    for account in accounts:
        for cutoff in cutoffs:
            expected = await _account_balance_at(session, account, cutoff)
            actual = _balance_from_preloaded(account, cutoff, preloaded)
            assert actual == pytest.approx(expected, abs=1e-6), (
                f"{account.name} at {cutoff}: preloaded {actual} != query {expected}"
            )


def _sweep(days_back: int = 12) -> list[date]:
    today = date.today()
    return [today - timedelta(days=n) for n in range(days_back, -2, -1)]


# ---------------------------------------------------------------------------
# Parity: manual accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parity_manual_account_running_balance(session: AsyncSession, test_user, test_workspace):
    acct = await _make_account(session, test_user.id, "Manual")
    today = date.today()
    await _add_txn(session, test_user.id, acct.id, "1000", "credit", today - timedelta(days=10))
    await _add_txn(session, test_user.id, acct.id, "250.55", "debit", today - timedelta(days=6))
    await _add_txn(session, test_user.id, acct.id, "80.10", "debit", today - timedelta(days=2))

    await _assert_parity(session, [acct], _sweep())


@pytest.mark.asyncio
async def test_parity_manual_account_same_day_rows(session: AsyncSession, test_user, test_workspace):
    """Several rows on one date collapse into a single prefix entry."""
    acct = await _make_account(session, test_user.id, "Manual same-day")
    today = date.today()
    day = today - timedelta(days=4)
    for amount in ("10.01", "20.02", "30.03"):
        await _add_txn(session, test_user.id, acct.id, amount, "debit", day)
    await _add_txn(session, test_user.id, acct.id, "500", "credit", today - timedelta(days=9))

    await _assert_parity(session, [acct], _sweep())


@pytest.mark.asyncio
async def test_parity_manual_account_ignores_pending(session: AsyncSession, test_user, test_workspace):
    """A pending row is not part of the posted-only balance."""
    acct = await _make_account(session, test_user.id, "Manual pending")
    today = date.today()
    await _add_txn(session, test_user.id, acct.id, "900", "credit", today - timedelta(days=8))
    await _add_txn(
        session, test_user.id, acct.id, "400", "debit", today - timedelta(days=3),
        status="pending",
    )

    preloaded = await _bulk_load_account_balance_data(session, [acct])
    assert _balance_from_preloaded(acct, today, preloaded) == pytest.approx(900.0)
    await _assert_parity(session, [acct], _sweep())


@pytest.mark.asyncio
async def test_parity_manual_account_ignores_flagged_transaction(session: AsyncSession, test_user, test_workspace):
    acct = await _make_account(session, test_user.id, "Manual ignored tx")
    today = date.today()
    await _add_txn(session, test_user.id, acct.id, "700", "credit", today - timedelta(days=7))
    await _add_txn(
        session, test_user.id, acct.id, "300", "debit", today - timedelta(days=5),
        is_ignored=True,
    )

    preloaded = await _bulk_load_account_balance_data(session, [acct])
    assert _balance_from_preloaded(acct, today, preloaded) == pytest.approx(700.0)
    await _assert_parity(session, [acct], _sweep())


@pytest.mark.asyncio
async def test_parity_manual_account_ignores_flagged_category(session: AsyncSession, test_user, test_workspace):
    """A row in an ignored category is excluded even when the row itself is not."""
    acct = await _make_account(session, test_user.id, "Manual ignored cat")
    ignored = await _make_category(session, test_user.id, test_workspace.id, "Ignored", is_ignored=True)
    counted = await _make_category(session, test_user.id, test_workspace.id, "Counted")
    today = date.today()
    await _add_txn(session, test_user.id, acct.id, "600", "credit", today - timedelta(days=7))
    await _add_txn(
        session, test_user.id, acct.id, "120", "debit", today - timedelta(days=4),
        category_id=counted.id,
    )
    await _add_txn(
        session, test_user.id, acct.id, "999", "debit", today - timedelta(days=3),
        category_id=ignored.id,
    )

    preloaded = await _bulk_load_account_balance_data(session, [acct])
    assert _balance_from_preloaded(acct, today, preloaded) == pytest.approx(480.0)
    await _assert_parity(session, [acct], _sweep())


@pytest.mark.asyncio
async def test_parity_manual_account_foreign_currency_row(session: AsyncSession, test_user, test_workspace):
    """A row in another currency contributes its primary amount, not its face value."""
    acct = await _make_account(session, test_user.id, "Manual FX", currency="BRL")
    today = date.today()
    await _add_txn(session, test_user.id, acct.id, "1000", "credit", today - timedelta(days=9))
    await _add_txn(
        session, test_user.id, acct.id, "100", "debit", today - timedelta(days=5),
        currency="USD", amount_primary="530.25",
    )

    preloaded = await _bulk_load_account_balance_data(session, [acct])
    assert _balance_from_preloaded(acct, today, preloaded) == pytest.approx(469.75)
    await _assert_parity(session, [acct], _sweep())


# ---------------------------------------------------------------------------
# Parity: connected accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parity_connected_account_walks_back_from_provider_balance(
    session: AsyncSession, test_user, test_workspace
):
    conn = await _make_connection(session, test_user.id)
    acct = await _make_account(
        session, test_user.id, "Connected", balance="2000", connection_id=conn.id
    )
    today = date.today()
    await _add_txn(session, test_user.id, acct.id, "150", "debit", today - timedelta(days=6))
    await _add_txn(session, test_user.id, acct.id, "75.25", "credit", today - timedelta(days=2))

    await _assert_parity(session, [acct], _sweep())


@pytest.mark.asyncio
async def test_connected_account_returns_provider_balance_at_today(
    session: AsyncSession, test_user, test_workspace
):
    """At or after today the provider number is returned verbatim."""
    conn = await _make_connection(session, test_user.id)
    acct = await _make_account(
        session, test_user.id, "Connected today", balance="1234.56", connection_id=conn.id
    )
    today = date.today()
    await _add_txn(session, test_user.id, acct.id, "99", "debit", today - timedelta(days=1))

    preloaded = await _bulk_load_account_balance_data(session, [acct])
    assert _balance_from_preloaded(acct, today, preloaded) == pytest.approx(1234.56)
    assert _balance_from_preloaded(
        acct, today + timedelta(days=30), preloaded
    ) == pytest.approx(1234.56)


@pytest.mark.asyncio
async def test_parity_connected_account_nets_out_provider_pending(
    session: AsyncSession, test_user, test_workspace
):
    """Provider snapshots include pending rows; historical points must drop them."""
    conn = await _make_connection(session, test_user.id)
    acct = await _make_account(
        session, test_user.id, "Connected pending", balance="800", connection_id=conn.id
    )
    today = date.today()
    await _add_txn(session, test_user.id, acct.id, "200", "debit", today - timedelta(days=5))
    await _add_txn(
        session, test_user.id, acct.id, "50", "debit", today - timedelta(days=1),
        status="pending",
    )

    await _assert_parity(session, [acct], _sweep())


@pytest.mark.asyncio
async def test_parity_connected_credit_card_sign_flip(session: AsyncSession, test_user, test_workspace):
    """A card's provider balance is debt, so the stored balance is negated."""
    conn = await _make_connection(session, test_user.id)
    card = await _make_account(
        session, test_user.id, "Card", acct_type="credit_card",
        balance="1500", connection_id=conn.id,
    )
    today = date.today()
    await _add_txn(session, test_user.id, card.id, "300", "debit", today - timedelta(days=7))
    await _add_txn(session, test_user.id, card.id, "120", "credit", today - timedelta(days=3))

    preloaded = await _bulk_load_account_balance_data(session, [card])
    assert _balance_from_preloaded(card, today, preloaded) == pytest.approx(-1500.0)
    await _assert_parity(session, [card], _sweep())


# ---------------------------------------------------------------------------
# Parity: mixed portfolios and edges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parity_mixed_portfolio(session: AsyncSession, test_user, test_workspace):
    """Every account shape at once, which is what the report actually loads."""
    conn = await _make_connection(session, test_user.id)
    ignored_cat = await _make_category(session, test_user.id, test_workspace.id, "Mixed ignored", is_ignored=True)
    today = date.today()

    manual = await _make_account(session, test_user.id, "Mixed manual")
    connected = await _make_account(
        session, test_user.id, "Mixed connected", balance="3000", connection_id=conn.id
    )
    card = await _make_account(
        session, test_user.id, "Mixed card", acct_type="credit_card",
        balance="450", connection_id=conn.id,
    )
    usd = await _make_account(session, test_user.id, "Mixed USD", currency="USD")

    await _add_txn(session, test_user.id, manual.id, "2500", "credit", today - timedelta(days=11))
    await _add_txn(session, test_user.id, manual.id, "310.40", "debit", today - timedelta(days=6))
    await _add_txn(
        session, test_user.id, manual.id, "60", "debit", today - timedelta(days=4),
        category_id=ignored_cat.id,
    )
    await _add_txn(session, test_user.id, connected.id, "420", "debit", today - timedelta(days=8))
    await _add_txn(
        session, test_user.id, connected.id, "90", "debit", today - timedelta(days=2),
        status="pending",
    )
    await _add_txn(session, test_user.id, card.id, "220", "debit", today - timedelta(days=5))
    await _add_txn(
        session, test_user.id, usd.id, "400", "credit", today - timedelta(days=9),
        currency="USD",
    )
    await _add_txn(
        session, test_user.id, usd.id, "150", "debit", today - timedelta(days=3),
        currency="BRL", amount_primary="28.75",
    )

    await _assert_parity(session, [manual, connected, card, usd], _sweep())


@pytest.mark.asyncio
async def test_parity_account_with_no_transactions(session: AsyncSession, test_user, test_workspace):
    """An empty ledger still has to resolve, not raise on the empty prefix."""
    manual = await _make_account(session, test_user.id, "Empty manual")
    conn = await _make_connection(session, test_user.id)
    connected = await _make_account(
        session, test_user.id, "Empty connected", balance="500", connection_id=conn.id
    )

    await _assert_parity(session, [manual, connected], _sweep(5))


@pytest.mark.asyncio
async def test_parity_cutoff_before_any_transaction(session: AsyncSession, test_user, test_workspace):
    """A cutoff earlier than every row lands on the zero end of the prefix."""
    acct = await _make_account(session, test_user.id, "Early cutoff")
    today = date.today()
    await _add_txn(session, test_user.id, acct.id, "100", "credit", today - timedelta(days=3))

    preloaded = await _bulk_load_account_balance_data(session, [acct])
    assert _balance_from_preloaded(acct, today - timedelta(days=30), preloaded) == pytest.approx(0.0)
    await _assert_parity(session, [acct], _sweep(40))


@pytest.mark.asyncio
async def test_parity_future_dated_rows_excluded(session: AsyncSession, test_user, test_workspace):
    """A row dated after today is forecast and never moves the current balance."""
    acct = await _make_account(session, test_user.id, "Future rows")
    today = date.today()
    await _add_txn(session, test_user.id, acct.id, "300", "credit", today - timedelta(days=2))
    await _add_txn(session, test_user.id, acct.id, "1000", "credit", today + timedelta(days=5))

    preloaded = await _bulk_load_account_balance_data(session, [acct])
    assert _balance_from_preloaded(acct, today, preloaded) == pytest.approx(300.0)
    assert _balance_from_preloaded(
        acct, today + timedelta(days=10), preloaded
    ) == pytest.approx(300.0)


@pytest.mark.asyncio
async def test_bulk_load_empty_account_list(session: AsyncSession, test_user, test_workspace):
    assert await _bulk_load_account_balance_data(session, []) == {}


@pytest.mark.asyncio
async def test_decimal_accumulation_does_not_drift(session: AsyncSession, test_user, test_workspace):
    """Many fractional rows must land on the exact cent, not a float approximation."""
    acct = await _make_account(session, test_user.id, "Cents")
    today = date.today()
    for n in range(60):
        await _add_txn(
            session, test_user.id, acct.id, "0.10", "debit",
            today - timedelta(days=30) + timedelta(days=n // 3),
        )
    await _add_txn(session, test_user.id, acct.id, "100", "credit", today - timedelta(days=31))

    preloaded = await _bulk_load_account_balance_data(session, [acct])
    assert _balance_from_preloaded(acct, today, preloaded) == pytest.approx(94.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Asset value preloading
# ---------------------------------------------------------------------------


async def _make_asset(session: AsyncSession, user_id: uuid.UUID, workspace_id: uuid.UUID, name: str) -> Asset:
    asset = Asset(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        name=name, type="real_estate", currency="BRL",
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


@pytest.mark.asyncio
async def test_asset_values_resolve_latest_entry_at_or_before_cutoff(
    session: AsyncSession, test_user, test_workspace
):
    asset = await _make_asset(session, test_user.id, test_workspace.id, "House")
    today = date.today()
    for offset, amount in ((20, "100000"), (10, "110000"), (3, "120000")):
        session.add(AssetValue(
            id=uuid.uuid4(), asset_id=asset.id,
            date=today - timedelta(days=offset), amount=Decimal(amount),
        ))
    await session.commit()

    preloaded = await _bulk_load_asset_values(session, [asset.id])
    assert _asset_value_from_preloaded(asset.id, today - timedelta(days=25), preloaded) is None
    assert _asset_value_from_preloaded(asset.id, today - timedelta(days=20), preloaded) == Decimal("100000")
    assert _asset_value_from_preloaded(asset.id, today - timedelta(days=11), preloaded) == Decimal("100000")
    assert _asset_value_from_preloaded(asset.id, today - timedelta(days=10), preloaded) == Decimal("110000")
    assert _asset_value_from_preloaded(asset.id, today, preloaded) == Decimal("120000")


@pytest.mark.asyncio
async def test_asset_values_same_date_takes_last_written(session: AsyncSession, test_user, test_workspace):
    """Two entries on one date resolve to the same one the ordered query picked."""
    asset = await _make_asset(session, test_user.id, test_workspace.id, "Land")
    today = date.today()
    day = today - timedelta(days=5)
    first = AssetValue(id=uuid.uuid4(), asset_id=asset.id, date=day, amount=Decimal("500"))
    second = AssetValue(id=uuid.uuid4(), asset_id=asset.id, date=day, amount=Decimal("900"))
    session.add_all([first, second])
    await session.commit()

    preloaded = await _bulk_load_asset_values(session, [asset.id])
    winner = max([first, second], key=lambda v: v.id).amount
    assert _asset_value_from_preloaded(asset.id, today, preloaded) == winner


@pytest.mark.asyncio
async def test_asset_values_unknown_asset_and_empty_input(session: AsyncSession, test_user, test_workspace):
    assert await _bulk_load_asset_values(session, []) == {}
    assert _asset_value_from_preloaded(uuid.uuid4(), date.today(), {}) is None


# ---------------------------------------------------------------------------
# Query count: the reason the preloading exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_net_worth_report_query_count_is_flat_across_the_trend(
    session: AsyncSession, test_user, test_workspace, monkeypatch
):
    """Query count must not scale with the number of trend points.

    A daily report over a year is ~365 points. If the per-point path ever
    reaches the database again, this count moves with the range and the whole
    preloading exercise is undone, so the test pins the shape rather than an
    exact number.
    """
    conn = await _make_connection(session, test_user.id)
    today = date.today()
    accounts = [
        await _make_account(session, test_user.id, "QC manual"),
        await _make_account(session, test_user.id, "QC connected", balance="1000", connection_id=conn.id),
        await _make_account(
            session, test_user.id, "QC card", acct_type="credit_card",
            balance="200", connection_id=conn.id,
        ),
    ]
    for acct in accounts:
        for n in range(5):
            await _add_txn(
                session, test_user.id, acct.id, "25", "debit",
                today - timedelta(days=40 + n * 3),
            )

    asset = await _make_asset(session, test_user.id, test_workspace.id, "QC house")
    session.add(AssetValue(
        id=uuid.uuid4(), asset_id=asset.id,
        date=today - timedelta(days=45), amount=Decimal("250000"),
    ))
    await session.commit()

    counter = {"n": 0}
    original = AsyncSession.execute

    async def counting_execute(self, *args, **kwargs):
        counter["n"] += 1
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", counting_execute)

    counter["n"] = 0
    await get_net_worth_report(
        session, test_workspace.id, test_user.id, months=2, interval="monthly"
    )
    monthly = counter["n"]

    counter["n"] = 0
    await get_net_worth_report(
        session, test_workspace.id, test_user.id, months=2, interval="daily"
    )
    daily = counter["n"]

    # Daily over the same window is roughly 30x the points of monthly. Some
    # slack is allowed for per-point work that is not a balance lookup, but a
    # per-point database hit would blow past this by an order of magnitude.
    assert daily <= monthly + 20, (
        f"query count scales with trend length: {monthly} monthly vs {daily} daily"
    )


@pytest.mark.asyncio
async def test_net_worth_report_matches_unprefetched_snapshots(
    session: AsyncSession, test_user, test_workspace
):
    """End to end: the report's own numbers still match the per-point path."""
    from app.services.report_service import _net_worth_at

    conn = await _make_connection(session, test_user.id)
    today = date.today()
    manual = await _make_account(session, test_user.id, "E2E manual")
    connected = await _make_account(
        session, test_user.id, "E2E connected", balance="4000", connection_id=conn.id
    )
    await _add_txn(session, test_user.id, manual.id, "1500", "credit", today - timedelta(days=50))
    await _add_txn(session, test_user.id, manual.id, "200", "debit", today - timedelta(days=20))
    await _add_txn(session, test_user.id, connected.id, "300", "debit", today - timedelta(days=15))

    report = await get_net_worth_report(
        session, test_workspace.id, test_user.id, months=2, interval="monthly"
    )

    # Recompute the final point the slow way and compare.
    expected = await _net_worth_at(session, test_workspace.id, today, "BRL")
    assert report.trend[-1].value == pytest.approx(expected.value, abs=0.01)
