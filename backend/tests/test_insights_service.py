"""Tests for the Insights historical reference.

The case that matters here is the sparse category: a month with no spend
produces no row, so a median taken only over the months that *have* rows
answers a different question than the one the screen asks.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.insights import PurchaseDecisionRequest
from app.services.insights_service import (
    _breakeven_net_gain,
    _historical_reference,
    get_breakeven_table,
    get_nature,
    get_purchase_decision,
)


async def _spend(
    session: AsyncSession,
    *,
    user: User,
    workspace: Workspace,
    account: Account,
    category: Category,
    when: date,
    amount: str,
) -> None:
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=user.id,
            workspace_id=workspace.id,
            account_id=account.id,
            category_id=category.id,
            amount=Decimal(amount),
            currency="BRL",
            type="debit",
            source="manual",
            status="posted",
            description="test spend",
            date=when,
            effective_date=when,
        )
    )


@pytest.mark.asyncio
async def test_historical_reference_counts_months_without_spend_as_zero(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
    test_categories: list[Category],
):
    """A category that spends in 2 of 6 months has a median of 0, not the
    median of the 2 months it happened to spend in.

    Without the zero-fill this returns ~100.00 — an order of magnitude above
    what the category actually averages — and every deviation shown against
    that reference inherits the error.
    """
    sparse = test_categories[0]
    steady = test_categories[1]

    # Six months of window; the sparse category appears in only two of them.
    months = [date(2026, m, 10) for m in range(1, 7)]
    for when in months:
        await _spend(
            session,
            user=test_user,
            workspace=test_workspace,
            account=test_account,
            category=steady,
            when=when,
            amount="200.00",
        )
    for when in (months[0], months[3]):
        await _spend(
            session,
            user=test_user,
            workspace=test_workspace,
            account=test_account,
            category=sparse,
            when=when,
            amount="100.00",
        )
    await session.commit()

    reference, method, months_used = await _historical_reference(
        session,
        test_workspace.id,
        "BRL",
        "accrual",
        target_month=date(2026, 7, 1),
        trusted_from=None,
    )

    assert months_used == 6
    assert method == "historical_median"
    # Four of six months are zero, so the median is zero.
    assert reference[sparse.id] == Decimal("0.00")
    # The steady category is unaffected by the zero-fill.
    assert reference[steady.id] == Decimal("200.00")


@pytest.mark.asyncio
async def test_historical_reference_excludes_the_target_month(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
    test_categories: list[Category],
):
    """The month being compared must not feed the reference it is compared
    against — including it drags the median toward the value under test and
    damps the deviation exactly when it matters."""
    category = test_categories[0]

    for when in (date(2026, 1, 10), date(2026, 2, 10), date(2026, 3, 10)):
        await _spend(
            session,
            user=test_user,
            workspace=test_workspace,
            account=test_account,
            category=category,
            when=when,
            amount="100.00",
        )
    # A large outlier inside the target month, which must be ignored.
    await _spend(
        session,
        user=test_user,
        workspace=test_workspace,
        account=test_account,
        category=category,
        when=date(2026, 4, 10),
        amount="9000.00",
    )
    await session.commit()

    reference, _method, months_used = await _historical_reference(
        session,
        test_workspace.id,
        "BRL",
        "accrual",
        target_month=date(2026, 4, 1),
        trusted_from=None,
    )

    assert months_used == 3
    assert reference[category.id] == Decimal("100.00")


# ---------------------------------------------------------------------------
# Breakeven table
# ---------------------------------------------------------------------------


def test_breakeven_net_gain_matches_known_correct_value():
    """R$ 5.000 in 12x at r=1,042%/month with IR 20% must yield a net gain
    of R$ 292,45 to the cent — the LOCKED tax model (IR charged once at
    redemption on total accumulated gross interest, not monthly and not
    proportionally per withdrawal). The two competing models give R$
    260,70 (IR monthly) and R$ 306,04 (IR proportional per withdrawal);
    both are wrong."""
    net_gain = _breakeven_net_gain(
        Decimal("5000"), 12, Decimal("0.01042"), Decimal("0.20")
    ).quantize(Decimal("0.01"))
    assert net_gain == Decimal("292.45")


def test_breakeven_table_has_rows_for_2_through_24_installments():
    table = get_breakeven_table(monthly_yield=0.01042)
    ns = [row.n for row in table.rows]
    assert ns == list(range(2, 25))
    # Sanity: gain grows with more installments (more time invested).
    assert table.rows[0].gain_per_1000 < table.rows[-1].gain_per_1000


# ---------------------------------------------------------------------------
# Nature (fixed / variable / discretionary)
# ---------------------------------------------------------------------------


async def _consumption_category(
    session: AsyncSession, *, user: User, name: str, expense_nature: str | None
) -> Category:
    cat = Category(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name,
        icon="circle-help",
        color="#6B7280",
        is_system=True,
        flow_type="consumption",
        expense_nature=expense_nature,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


@pytest.mark.asyncio
async def test_nature_month_with_zero_spend_returns_shares_none(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
):
    """A month with no consumption spend must return shares=None, not a
    zeroed-out breakdown that lies about the month being empty."""
    # Category exists but no transaction is created this month at all —
    # the single-month series (months=1) has zero consumption spend.
    await _consumption_category(session, user=test_user, name="Fixo", expense_nature="fixed")

    data = await get_nature(session, test_workspace.id, "BRL", months=1)

    assert len(data.series) == 1
    current = data.series[0]
    assert current.shares is None
    assert current.values.fixed == Decimal("0.00") or current.values.fixed == Decimal("0")


@pytest.mark.asyncio
async def test_nature_transaction_override_beats_category_default(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
):
    """A transaction-level `expense_nature` override must win over its
    category's default nature."""
    category = await _consumption_category(
        session, user=test_user, name="Variável", expense_nature="variable"
    )
    today = date.today().replace(day=15)
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            category_id=category.id,
            amount=Decimal("300.00"),
            currency="BRL",
            type="debit",
            source="manual",
            status="posted",
            description="override test",
            date=today,
            effective_date=today,
            expense_nature="discretionary",
        )
    )
    await session.commit()

    data = await get_nature(session, test_workspace.id, "BRL", months=1)

    current = data.series[0]
    assert current.values.discretionary == Decimal("300.00")
    assert current.values.variable == Decimal("0.00") or current.values.variable == Decimal("0")


@pytest.mark.asyncio
async def test_nature_null_effective_nature_lands_in_unclassified(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
):
    """When neither the transaction nor its category sets an expense
    nature, the effective nature is NULL and the spend must land in
    `unclassified` rather than being silently dropped."""
    category = await _consumption_category(
        session, user=test_user, name="Sem natureza", expense_nature=None
    )
    today = date.today().replace(day=10)
    await _spend(
        session,
        user=test_user,
        workspace=test_workspace,
        account=test_account,
        category=category,
        when=today,
        amount="150.00",
    )
    await session.commit()

    data = await get_nature(session, test_workspace.id, "BRL", months=1)

    current = data.series[0]
    assert current.values.unclassified == Decimal("150.00")
    total = (
        current.values.fixed
        + current.values.variable
        + current.values.discretionary
        + current.values.unclassified
    )
    assert total == Decimal("150.00")


# ---------------------------------------------------------------------------
# Purchase decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_decision_blocks_upfront_without_available_cash(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    """If the user cannot cover the upfront amount from checking/savings
    balances, upfront must not be recommended regardless of the math."""
    # test_workspace has no accounts yet in this test — available cash is 0.
    request = PurchaseDecisionRequest(
        price=Decimal("500.00"),
        cash_discount_pct=0.5,
        installments=3,
    )
    result = await get_purchase_decision(session, test_workspace.id, "BRL", request)
    assert result.verdict.choice == "installments"


@pytest.mark.asyncio
async def test_purchase_decision_with_revolving_debt_prefers_cash(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
):
    """If the user carries revolving credit-card debt, paying cash must
    win regardless of the installments' cash-discount offer, because the
    opportunity cost is the card's rate, not the CDB's."""
    from app.models.bank_connection import BankConnection

    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        provider="test",
        external_id="cc-conn",
        institution_name="Banco Teste",
        credentials={"token": "fake"},
        status="active",
    )
    session.add(conn)
    await session.flush()

    cc_account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        connection_id=conn.id,
        external_id="cc-ext",
        name="Cartão Teste",
        type="credit_card",
        balance=Decimal("-1000.00"),
        currency="BRL",
    )
    session.add(cc_account)
    # Plenty of cash available, so the cash precondition alone wouldn't
    # explain a "cash" verdict — it must be the revolving-debt check.
    test_account.balance = Decimal("10000.00")
    await session.commit()

    request = PurchaseDecisionRequest(
        price=Decimal("500.00"),
        cash_discount_pct=0.0,
        installments=12,
    )
    result = await get_purchase_decision(session, test_workspace.id, "BRL", request)
    assert result.verdict.choice == "cash"
