"""Tests for the Insights historical reference.

The case that matters here is the sparse category: a month with no spend
produces no row, so a median taken only over the months that *have* rows
answers a different question than the one the screen asks.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.insights import PurchaseDecisionRequest
from app.services.insights_service import (
    _breakeven_net_gain,
    _historical_reference,
    get_breakeven_table,
    get_flow,
    get_goals,
    get_nature,
    get_projection,
    get_purchase_decision,
    get_vitals,
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


# ---------------------------------------------------------------------------
# Flow (Sankey) — invariant test
# ---------------------------------------------------------------------------


async def _income_category(session: AsyncSession, *, user: User, name: str = "Salário") -> Category:
    cat = Category(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name,
        icon="circle-help",
        color="#22C55E",
        is_system=True,
        flow_type="income",
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


async def _consumption_category_in_group(
    session: AsyncSession,
    *,
    user: User,
    name: str,
    group: CategoryGroup,
) -> Category:
    cat = Category(
        id=uuid.uuid4(),
        user_id=user.id,
        group_id=group.id,
        name=name,
        icon="circle-help",
        color="#F59E0B",
        is_system=True,
        flow_type="consumption",
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


async def _credit(
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
            type="credit",
            source="manual",
            status="posted",
            description="test income",
            date=when,
            effective_date=when,
        )
    )


@pytest.mark.asyncio
async def test_flow_edges_sum_to_node_value_within_a_cent(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
):
    """For every node in the Sankey, the sum of its outgoing edges must equal
    the node's own value (within a cent). This is the invariant the design's
    mock caught a real bug with — it must hold for the real endpoint too."""
    income_cat = await _income_category(session, user=test_user)
    group = CategoryGroup(
        id=uuid.uuid4(), user_id=test_user.id, name="Despesas", icon="folder", color="#6B7280"
    )
    session.add(group)
    await session.commit()
    await session.refresh(group)

    cat_a = await _consumption_category_in_group(session, user=test_user, name="Grande", group=group)
    cat_b = await _consumption_category_in_group(session, user=test_user, name="Pequena", group=group)

    today = date.today().replace(day=15)
    await _credit(
        session, user=test_user, workspace=test_workspace, account=test_account,
        category=income_cat, when=today, amount="5000.00",
    )
    await _spend(
        session, user=test_user, workspace=test_workspace, account=test_account,
        category=cat_a, when=today, amount="2000.00",
    )
    # Below the 3% collapse threshold of income (5000 * 0.03 = 150.00).
    await _spend(
        session, user=test_user, workspace=test_workspace, account=test_account,
        category=cat_b, when=today, amount="50.00",
    )
    await session.commit()

    data = await get_flow(session, test_workspace.id, "BRL", month=today)

    outgoing: dict[str, Decimal] = {}
    for link in data.links:
        outgoing[link.source] = outgoing.get(link.source, Decimal("0")) + link.value

    for node in data.nodes:
        node_outgoing = outgoing.get(node.id, Decimal("0"))
        # Leaf nodes (no outgoing edges) trivially satisfy the invariant.
        if node.id not in outgoing:
            continue
        assert abs(node_outgoing - node.value) <= Decimal("0.01"), (
            f"node {node.id} has value {node.value} but outgoing edges sum to {node_outgoing}"
        )

    # The small category must have collapsed into "Outros".
    other_nodes = [n for n in data.nodes if n.label == "Outros"]
    assert len(other_nodes) == 1
    assert other_nodes[0].value == Decimal("50.00")
    assert data.collapse_threshold == Decimal("50.00")


# ---------------------------------------------------------------------------
# Vitals — savings_rate uses (income - consumption) / income
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_savings_rate_uses_income_minus_consumption_not_net_contribution(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
):
    """savings_rate is LOCKED to (income - consumption) / income. A transfer
    to savings (flow_type='saving', not 'consumption') must not reduce it —
    if it did, the endpoint would be computing net contribution instead."""
    income_cat = await _income_category(session, user=test_user)
    group = CategoryGroup(
        id=uuid.uuid4(), user_id=test_user.id, name="Despesas", icon="folder", color="#6B7280"
    )
    session.add(group)
    await session.commit()
    await session.refresh(group)
    consumption_cat = await _consumption_category_in_group(
        session, user=test_user, name="Alimentação", group=group
    )
    saving_cat = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Poupança",
        icon="circle-help",
        color="#0EA5E9",
        is_system=True,
        flow_type="saving",
    )
    session.add(saving_cat)
    await session.commit()
    await session.refresh(saving_cat)

    today = date.today().replace(day=10)
    await _credit(
        session, user=test_user, workspace=test_workspace, account=test_account,
        category=income_cat, when=today, amount="4000.00",
    )
    await _spend(
        session, user=test_user, workspace=test_workspace, account=test_account,
        category=consumption_cat, when=today, amount="1000.00",
    )
    # Money moved to savings — must NOT lower the LOCKED savings_rate.
    await _spend(
        session, user=test_user, workspace=test_workspace, account=test_account,
        category=saving_cat, when=today, amount="2000.00",
    )
    await session.commit()

    cards = await get_vitals(session, test_workspace.id, "BRL")
    savings_card = next(c for c in cards if c.key == "savings_rate")

    # (4000 - 1000) / 4000 = 0.75 -> 75.00%, NOT (4000-1000-2000)/4000 = 25%.
    assert savings_card.value == Decimal("75.00")


# ---------------------------------------------------------------------------
# Goals — zero contribution -> stalled, no estimated_completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goal_with_zero_contribution_is_stalled_with_no_completion_date(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
):
    """A goal whose tracked account never received a qualifying credit has
    an observed contribution of 0 (or less). Projecting a completion date
    from that yields infinity; the correct answer is status='stalled' and
    estimated_completion=None — matching the real data (a goal with
    current_amount=0.00 and a target_date days away)."""
    goal = Goal(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Reserva de Emergência 10k",
        target_amount=Decimal("10000.00"),
        current_amount=Decimal("0.00"),
        currency="BRL",
        target_date=date.today() + timedelta(days=13),
        tracking_type="account",
        account_id=test_account.id,
        status="active",
    )
    session.add(goal)
    await session.commit()

    # No credits at all land in test_account over the trailing window, so
    # observed_contribution must be 0.
    rows = await get_goals(session, test_workspace.id, "BRL")
    assert len(rows) == 1
    row = rows[0]

    assert row.estimated_completion is None
    assert row.status == "stalled"


# ---------------------------------------------------------------------------
# Projection — confidence band grows with sqrt(n), not linearly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_projection_band_half_width_grows_with_sqrt_n_not_linearly(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
    test_categories: list[Category],
):
    """hw(n) = hw(1) * sqrt(n). At month 6, the half-width must be sqrt(6)
    (~2.449x) the month-1 half-width, NOT 6x — a linear band would be 3.3x
    too wide at 12 months and disarm the cash-gap sanity check."""
    consumption_cat = test_categories[0]
    consumption_cat.flow_type = "consumption"
    income_cat = test_categories[2]
    income_cat.flow_type = "income"
    await session.commit()

    today = date.today()
    # Vary variable spend across trailing months so p75-p25 is nonzero.
    amounts = ["500.00", "700.00", "900.00", "1100.00", "1300.00", "1500.00"]
    for i, amount in enumerate(amounts, start=1):
        when = _shift_month_for_test(today, -i)
        await _spend(
            session, user=test_user, workspace=test_workspace, account=test_account,
            category=consumption_cat, when=when, amount=amount,
        )
        await _credit(
            session, user=test_user, workspace=test_workspace, account=test_account,
            category=income_cat, when=when, amount="5000.00",
        )
    await session.commit()

    data = await get_projection(session, test_workspace.id, "BRL")
    projected = [p for p in data.points if p.kind == "projected"]
    assert len(projected) >= 6
    assert projected[0].low is not None and projected[0].high is not None
    assert projected[5].low is not None and projected[5].high is not None

    hw1 = (projected[0].high - projected[0].low) / 2
    hw6 = (projected[5].high - projected[5].low) / 2

    assert hw1 > 0
    ratio = float(hw6 / hw1)
    # sqrt(6) ~= 2.449 -- must be well under 6 (linear) and close to sqrt(6).
    assert ratio < 3.0
    assert ratio > 2.0


def _shift_month_for_test(d: date, delta: int) -> date:
    total = (d.year * 12 + (d.month - 1)) + delta
    year, month0 = divmod(total, 12)
    from calendar import monthrange

    day = min(d.day, monthrange(year, month0 + 1)[1])
    return date(year, month0 + 1, day)


# ---------------------------------------------------------------------------
# Vitals — runway includes installments in the denominator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runway_denominator_includes_peak_installment_month(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
    test_categories: list[Category],
):
    """Essential monthly cost = median(fixed+variable consumption) + peak
    installment month over the next 6 months. A large upcoming installment
    must shrink the runway relative to a scenario with no installments —
    installments are legally owed, so they belong in the denominator."""
    consumption_cat = test_categories[0]
    consumption_cat.flow_type = "consumption"
    consumption_cat.expense_nature = "fixed"
    await session.commit()

    today = date.today()
    reserve = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Reserva de Emergência",
        type="savings",
        balance=Decimal("6000.00"),
        currency="BRL",
    )
    session.add(reserve)
    await session.commit()
    await session.refresh(reserve)

    # Manual (unconnected) accounts derive their balance by summing signed
    # transactions rather than reading `Account.balance` directly — seed an
    # opening deposit so the reserve actually carries 6000.00.
    opening = date(2020, 1, 1)
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=reserve.id,
            amount=Decimal("6000.00"),
            currency="BRL",
            type="credit",
            source="manual",
            status="posted",
            description="saldo inicial",
            date=opening,
            effective_date=opening,
        )
    )
    await session.commit()

    # 3 trailing months of steady essential spend (meets MIN_MONTHS_FOR_RUNWAY).
    for i in range(1, 4):
        when = _shift_month_for_test(today, -i)
        await _spend(
            session, user=test_user, workspace=test_workspace, account=test_account,
            category=consumption_cat, when=when, amount="1000.00",
        )
    await session.commit()

    cards_no_installments = await get_vitals(session, test_workspace.id, "BRL")
    runway_before = next(c for c in cards_no_installments if c.key == "runway")
    assert runway_before.available

    # A large installment due next month must reduce the runway.
    next_month = _shift_month_for_test(today, 1)
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            category_id=consumption_cat.id,
            amount=Decimal("3000.00"),
            currency="BRL",
            type="debit",
            source="manual",
            status="posted",
            description="parcela grande",
            date=next_month,
            effective_date=next_month,
            total_installments=6,
            installment_number=1,
        )
    )
    await session.commit()

    cards_with_installments = await get_vitals(session, test_workspace.id, "BRL")
    runway_after = next(c for c in cards_with_installments if c.key == "runway")

    assert runway_after.available
    assert runway_after.value is not None
    assert runway_before.value is not None
    assert runway_after.value < runway_before.value
