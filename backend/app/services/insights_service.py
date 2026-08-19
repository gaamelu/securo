"""Service layer for the Insights tab (GET /api/insights/*).

Income-independent endpoints (hygiene, categories, nature, breakeven table,
purchase decision) plus the income-dependent ones (vitals, flow, projection,
goals, alerts) added afterward. See `backend/app/schemas/insights.py` for the
response contracts and `backend/app/services/_query_filters.py` for the
shared P&L definition these queries reuse rather than duplicate.
"""
import math
import statistics
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.asset_value import AssetValue
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.schemas.insights import (
    AlertLink,
    AlertRow,
    BreakevenRow,
    BreakevenTableData,
    CategoriesData,
    CategoryRow,
    FlexibilityImpact,
    FlowData,
    FlowLink,
    FlowNode,
    GoalRow,
    HygieneCoverage,
    HygieneCoverageStat,
    HygieneData,
    HygieneOffender,
    InsightsEnvelope,
    InsightsError,
    InsightsWindow,
    NatureData,
    NatureMonth,
    NatureShares,
    NatureValues,
    ProjectionAssumption,
    ProjectionComponents,
    ProjectionData,
    ProjectionPoint,
    PurchaseDecisionData,
    PurchaseDecisionRequest,
    PurchaseDecisionScheduleRow,
    PurchaseDecisionVerdict,
    SavingsDestination,
    VitalCard,
    VitalReference,
    VitalSeriesPoint,
    VitalTrend,
    YieldBasis,
    YieldRange,
    YieldWindowDays,
)
from app.services._query_filters import counts_as_user_pnl, reporting_date_col
from app.services.insights_diagnostics import nature_shares, projection_months, savings_rate, security_status
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.credit_card_service import compute_available_credit
from app.services.dashboard_service import (
    _account_balance_at,
    _get_open_accounts,
    _get_recurring_projections,
    _month_range,
)
from app.services.fx_rate_service import convert
from app.services.budget_service import get_budgets
from app.services.report_service import _ACCOUNT_TYPE_COLORS, _ASSET_TYPE_COLORS  # noqa: F401 (color palette reuse)

# Default monthly gross yield used for the breakeven table / purchase
# decision calculator until a real observed-yield data source exists.
DEFAULT_MONTHLY_YIELD = 0.01042

# Brazilian IR-on-fixed-income brackets by holding period (days).
_IR_BRACKETS: list[tuple[int, Decimal]] = [
    (180, Decimal("0.225")),
    (360, Decimal("0.20")),
    (720, Decimal("0.175")),
]
_IR_BEYOND = Decimal("0.15")

# Nature natures that participate in the fixed/variable/discretionary split.
_NATURE_KEYS = ("fixed", "variable", "discretionary", "unclassified")

# Minimum number of trusted historical months required before a median
# reference can be trusted. Below this the sample is too thin to mean
# anything, so the endpoint reports insufficient history instead of a
# number that looks precise but isn't.
MIN_MONTHS_FOR_MEDIAN = 6

# Safety reference uses six complete months. A shorter sample produces a
# visually precise but financially weak baseline.
MIN_MONTHS_FOR_RUNWAY = 6

# How many worst offenders to surface in the hygiene block.
_WORST_OFFENDERS_LIMIT = 5

# Legacy emergency-fund account name. Excluded from net_worth (see
# get_vitals) because it mirrors Pluggy CDB assets already counted there.
# Runway/safety calculations use every open `savings` account instead.
_EMERGENCY_FUND_ACCOUNT_NAME = "Reserva de Emergência"

# Modified z-score constant (Iglewicz & Hoaglin). Omitting it makes the
# |z| > 3.5 threshold fire on ~31% of stable categories instead of the
# intended tail.
_MODIFIED_ZSCORE_CONSTANT = 0.6745
_MODIFIED_ZSCORE_THRESHOLD = 3.5


def _transaction_primary_amount(primary_currency: str):
    """Use stored transaction conversion when FX rate table has no quote.

    `amount_primary` is the authoritative value for manually confirmed or
    provider-derived foreign-currency rows. Rows without it retain native
    amount fallback, matching existing live-read behavior.
    """
    return case(
        (Transaction.currency == primary_currency, Transaction.amount),
        else_=func.coalesce(Transaction.amount_primary, Transaction.amount),
    )


async def _get_trusted_from(session: AsyncSession, workspace_id: uuid.UUID) -> Optional[date]:
    """Return the workspace's `insights.trusted_from` setting, or None.

    # TODO(passo-6): there is no workspace-scoped settings table yet for
    # insights-specific config. Once one exists, read the real
    # `insights.trusted_from` value for `workspace_id` here. Until then this
    # is a defensive stub that always returns None (no trusted-window floor),
    # which callers must treat as "use the whole available history".
    """
    return None


# ---------------------------------------------------------------------------
# Hygiene
# ---------------------------------------------------------------------------


async def get_hygiene(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
) -> HygieneData:
    """Data-quality snapshot: categorization, nature, and transfer-pairing
    coverage, plus the worst uncategorized offenders by summed amount."""

    total_result = await session.execute(
        select(func.count(Transaction.id)).where(
            Transaction.workspace_id == workspace_id,
            Transaction.source != "opening_balance",
        )
    )
    total_txs = total_result.scalar_one()

    categorized_result = await session.execute(
        select(func.count(Transaction.id)).where(
            Transaction.workspace_id == workspace_id,
            Transaction.source != "opening_balance",
            Transaction.category_id.is_not(None),
        )
    )
    categorized_txs = categorized_result.scalar_one()

    # Nature coverage denominator is consumption transactions only — income
    # has no nature, and including it would understate coverage.
    consumption_total_result = await session.execute(
        select(func.count(Transaction.id))
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Category.flow_type == "consumption",
        )
    )
    consumption_total = consumption_total_result.scalar_one()

    nature_set_result = await session.execute(
        select(func.count(Transaction.id))
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Category.flow_type == "consumption",
            func.coalesce(Transaction.expense_nature, Category.expense_nature).is_not(None),
        )
    )
    nature_set = nature_set_result.scalar_one()

    # Transfers-paired coverage: of transactions in transfer-flagged
    # categories, how many have a `transfer_pair_id` set.
    transfer_total_result = await session.execute(
        select(func.count(Transaction.id))
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Category.flow_type == "transfer",
        )
    )
    transfer_total = transfer_total_result.scalar_one()

    transfer_paired_result = await session.execute(
        select(func.count(Transaction.id))
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Category.flow_type == "transfer",
            Transaction.transfer_pair_id.is_not(None),
        )
    )
    transfer_paired = transfer_paired_result.scalar_one()

    coverage = HygieneCoverage(
        categorized=HygieneCoverageStat(done=categorized_txs, total=total_txs),
        nature_set=HygieneCoverageStat(done=nature_set, total=consumption_total),
        transfers_paired=HygieneCoverageStat(done=transfer_paired, total=transfer_total),
    )

    worst_offenders = await _get_worst_offenders(session, workspace_id, primary_currency)

    return HygieneData(
        coverage=coverage,
        last_review_at=None,
        days_since_review=None,
        worst_offenders=worst_offenders,
    )


async def _get_worst_offenders(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
) -> list[HygieneOffender]:
    """Uncategorized transactions grouped by (month, account, payee), top 5
    by summed absolute amount — a single large uncategorized transfer
    outweighs many small uncategorized purchases."""
    accounting_mode = await get_credit_card_accounting_mode(session)
    date_col = reporting_date_col(accounting_mode)

    # The month is only a grouping label here, so it is truncated in Python
    # rather than with `date_trunc` — that function is Postgres-only and the
    # test suite runs on SQLite.
    result = await session.execute(
        select(
            date_col.label("reported_on"),
            Transaction.account_id,
            Transaction.payee,
            Transaction.currency,
            Transaction.amount,
            Transaction.amount_primary,
        ).where(
            Transaction.workspace_id == workspace_id,
            Transaction.source != "opening_balance",
            Transaction.category_id.is_(None),
        )
    )
    rows = result.all()

    # Multi-currency: convert each (month, account, payee, currency) bucket
    # to primary currency before ranking, then merge same (month, account,
    # payee) buckets that differ only by currency.
    merged: dict[tuple, dict] = {}
    for reported_on, account_id, payee, currency, amount, amount_primary in rows:
        month = reported_on.replace(day=1) if reported_on else None
        key = (month, account_id, payee)
        if currency == primary_currency:
            converted = Decimal(str(abs(amount or 0)))
        elif amount_primary is not None:
            converted = Decimal(str(abs(amount_primary)))
        else:
            converted, _ = await convert(
                session, Decimal(str(abs(amount or 0))), currency, primary_currency
            )
        bucket = merged.setdefault(key, {"missing": 0, "amount": Decimal("0")})
        bucket["missing"] += 1
        bucket["amount"] += converted

    account_names = await _account_names(session, workspace_id)

    ranked = sorted(merged.items(), key=lambda kv: kv[1]["amount"], reverse=True)[:_WORST_OFFENDERS_LIMIT]

    offenders: list[HygieneOffender] = []
    for (month, account_id, payee), agg in ranked:
        account_name = account_names.get(account_id, "Conta desconhecida")
        payee_label = payee or "Sem beneficiário"
        month_label = month.strftime("%Y-%m") if month else "Sem data"
        label = f"{month_label} · {account_name} · {payee_label}"
        offenders.append(
            HygieneOffender(
                kind="payee",
                label=label,
                missing=agg["missing"],
                amount=agg["amount"],
            )
        )
    return offenders


async def _account_names(session: AsyncSession, workspace_id: uuid.UUID) -> dict:
    from app.models.account import Account

    result = await session.execute(
        select(Account.id, Account.display_name, Account.name).where(Account.workspace_id == workspace_id)
    )
    return {row[0]: (row[1] or row[2]) for row in result.all()}


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


async def get_categories(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    reference: Literal["budget", "historical"],
    month: Optional[date] = None,
) -> InsightsEnvelope[CategoriesData]:
    """Per-category actual spend for a month vs. a reference (budget or
    trusted-history median)."""
    accounting_mode = await get_credit_card_accounting_mode(session)
    date_col = reporting_date_col(accounting_mode)

    target_month = _month_start(month or date.today())
    next_month = _add_month(target_month)

    # Actual spend per category for the target month, converted to primary.
    actual_result = await session.execute(
        select(
            Transaction.category_id,
            func.sum(_transaction_primary_amount(primary_currency)).label("total"),
            func.count(Transaction.id).label("tx_count"),
        )
        .where(
            Transaction.workspace_id == workspace_id,
            date_col >= target_month,
            date_col < next_month,
            Transaction.type == "debit",
            counts_as_user_pnl(),
        )
        .group_by(Transaction.category_id)
    )
    actual_rows = actual_result.all()

    actual_by_category: dict[Optional[uuid.UUID], dict] = {}
    for category_id, total, tx_count in actual_rows:
        bucket = actual_by_category.setdefault(category_id, {"amount": Decimal("0"), "tx_count": 0})
        bucket["amount"] += Decimal(str(total or 0))
        bucket["tx_count"] += tx_count

    categories = await _get_categories_meta(session, workspace_id)

    if reference == "budget":
        reference_by_category, reference_method = await _budget_reference(
            session, workspace_id, primary_currency, target_month
        )
        window = standard_insights_window()
    else:
        trusted_from = await _get_trusted_from(session, workspace_id)
        reference_by_category, reference_method, months_used = await _historical_reference(
            session, workspace_id, primary_currency, accounting_mode, target_month, trusted_from
        )
        if months_used < MIN_MONTHS_FOR_MEDIAN:
            return InsightsEnvelope[CategoriesData](
                data=None,
                error=InsightsError(
                    code="INSUFFICIENT_HISTORY",
                    message=(
                        f"Histórico insuficiente para calcular a referência: "
                        f"{months_used} de {MIN_MONTHS_FOR_MEDIAN} meses confiáveis disponíveis."
                    ),
                    retryable=False,
                ),
                window=standard_insights_window(),
                reference=reference,
                currency=primary_currency,
                generated_at=datetime.now(timezone.utc),
            )
        window = standard_insights_window()

    rows: list[CategoryRow] = []
    all_category_ids = set(actual_by_category.keys()) | set(reference_by_category.keys())
    for category_id in all_category_ids:
        meta = categories.get(category_id)
        actual = actual_by_category.get(category_id, {"amount": Decimal("0"), "tx_count": 0})
        amount = actual["amount"]
        tx_count = actual["tx_count"]
        ref_value = reference_by_category.get(category_id)

        delta = None
        delta_pct = None
        status: str
        if ref_value is None:
            status = "no_ref"
        else:
            delta = amount - ref_value
            delta_pct = float(delta / ref_value) if ref_value != 0 else None
            if amount > ref_value:
                status = "over"
            elif amount < ref_value:
                status = "under"
            else:
                status = "ok"

        rows.append(
            CategoryRow(
                category_id=str(category_id) if category_id else None,
                label=meta["label"] if meta else "Sem categoria",
                color=meta["color"] if meta else "#6B7280",
                group_label=meta["group_label"] if meta else "Sem grupo",
                nature=meta["nature"] if meta else None,
                amount=amount,
                reference=ref_value,
                reference_method=reference_method if ref_value is not None else None,
                delta=delta,
                delta_pct=delta_pct,
                tx_count=tx_count,
                status=status,
            )
        )

    rows.sort(key=lambda r: r.amount, reverse=True)

    data = CategoriesData(rows=rows, available=True, blocked_reason=None)
    return InsightsEnvelope[CategoriesData](
        data=data,
        error=None,
        window=window,
        reference=reference,
        currency=primary_currency,
        generated_at=datetime.now(timezone.utc),
    )


def _add_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


async def _get_categories_meta(session: AsyncSession, workspace_id: uuid.UUID) -> dict:
    result = await session.execute(
        select(Category, CategoryGroup.name)
        .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
        .where(Category.workspace_id == workspace_id)
    )
    out = {}
    for category, group_name in result.all():
        out[category.id] = {
            "label": category.name,
            "color": category.color,
            "group_label": group_name or "Sem grupo",
            "nature": category.expense_nature,
        }
    return out


async def _budget_reference(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    target_month: date,
) -> tuple[dict, str]:
    """Reference = the category's budget for `target_month`, converted to
    primary currency."""
    budgets = await get_budgets(session, workspace_id, target_month)
    out: dict[Optional[uuid.UUID], Decimal] = {}
    for budget in budgets:
        ccy = budget.currency or primary_currency
        converted, _ = await convert(session, Decimal(str(budget.amount)), ccy, primary_currency)
        out[budget.category_id] = converted
    return out, "budget"


async def _historical_reference(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    accounting_mode: str,
    target_month: date,
    trusted_from: Optional[date],
) -> tuple[dict, str, int]:
    """Reference = median monthly spend per category across trusted months,
    excluding the current month (including it would drag the median toward
    the value being compared, damping the deviation exactly when it
    matters)."""
    date_col = reporting_date_col(accounting_mode)

    # Month bucketing is done by date range per month rather than with
    # `date_trunc`: that function is Postgres-only, and the test suite runs
    # on SQLite. `_month_range` is the same helper the dashboard and report
    # services already bucket with.
    window_start = trusted_from.replace(day=1) if trusted_from else None
    if window_start is None:
        earliest = await session.scalar(
            select(func.min(date_col)).where(
                Transaction.workspace_id == workspace_id,
                Transaction.type == "debit",
                date_col < target_month,
                counts_as_user_pnl(),
            )
        )
        if earliest is None:
            return {}, "historical_median", 0
        window_start = earliest.replace(day=1)

    months: list[date] = []
    cursor = window_start
    target_start = target_month.replace(day=1)
    while cursor < target_start:
        months.append(cursor)
        cursor = _month_range(cursor)[1]

    per_category_month: dict[tuple, Decimal] = {}
    months_seen: set = set()
    for month_start in months:
        _start, month_end = _month_range(month_start)
        result = await session.execute(
            select(
                Transaction.category_id,
                func.sum(_transaction_primary_amount(primary_currency)).label("total"),
            )
            .where(
                Transaction.workspace_id == workspace_id,
                Transaction.type == "debit",
                date_col >= month_start,
                date_col < month_end,
                counts_as_user_pnl(),
            )
            .group_by(Transaction.category_id)
        )
        months_seen.add(month_start)
        for category_id, total in result.all():
            key = (category_id, month_start)
            per_category_month[key] = per_category_month.get(key, Decimal("0")) + Decimal(str(total or 0))

    months_used = len(months_seen)

    # A month without spend in a category produces no row at all, so the
    # median has to run over *every* month in the window rather than only the
    # months that happen to have one. Without this zero-fill, "Educação"
    # (spend in 2 of 11 months) takes the median of those 2 months as its
    # reference — roughly an order of magnitude above what it actually
    # averages, and the deviation shown against it is wrong by the same factor.
    per_category_values: dict[Optional[uuid.UUID], list[Decimal]] = {}
    for category_id in {cat_id for cat_id, _month in per_category_month}:
        per_category_values[category_id] = [
            per_category_month.get((category_id, month), Decimal("0"))
            for month in months_seen
        ]

    out: dict[Optional[uuid.UUID], Decimal] = {}
    for category_id, values in per_category_values.items():
        floats = [float(v) for v in values]
        out[category_id] = Decimal(str(statistics.median(floats))).quantize(Decimal("0.01"))

    return out, "historical_median", months_used


# ---------------------------------------------------------------------------
# Nature (fixed / variable / discretionary)
# ---------------------------------------------------------------------------


async def get_nature(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    months: int = 12,
) -> NatureData:
    """Fixed / variable / discretionary spend split, one entry per month,
    over the last `months` months (most recent last).

    Effective nature per transaction is
    `COALESCE(transactions.expense_nature, categories.expense_nature)` — a
    transaction-level override falling back to the category's default. Only
    `flow_type='consumption'` categories participate; transactions whose
    effective nature is NULL land in the `unclassified` bucket rather than
    being dropped.
    """
    accounting_mode = await get_credit_card_accounting_mode(session)
    date_col = reporting_date_col(accounting_mode)

    today = date.today()
    current_month_start = _month_start(today)

    # Oldest-to-newest list of the `months` month-starts ending at the
    # current month, inclusive.
    month_starts: list[date] = []
    cursor = current_month_start
    for _ in range(months):
        month_starts.append(cursor)
        cursor = _shift_month(cursor, -1)
    month_starts.reverse()
    month_totals = await _monthly_totals(
        session, workspace_id, primary_currency, accounting_mode, month_starts
    )

    effective_nature = func.coalesce(Transaction.expense_nature, Category.expense_nature)

    series: list[NatureMonth] = []
    for month_start in month_starts:
        month_end = _month_range(month_start)[1]
        result = await session.execute(
            select(
                effective_nature.label("nature"),
                func.sum(_transaction_primary_amount(primary_currency)).label("total"),
            )
            .join(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.workspace_id == workspace_id,
                Transaction.type == "debit",
                Category.flow_type == "consumption",
                date_col >= month_start,
                date_col < month_end,
                counts_as_user_pnl(),
            )
            .group_by(effective_nature)
        )
        rows = result.all()

        totals: dict[str, Decimal] = {key: Decimal("0") for key in _NATURE_KEYS}
        for nature, total in rows:
            key = nature if nature in ("fixed", "variable", "discretionary") else "unclassified"
            totals[key] += Decimal(str(total or 0))

        month_total = sum(totals.values(), Decimal("0"))
        values = NatureValues(
            fixed=totals["fixed"],
            variable=totals["variable"],
            discretionary=totals["discretionary"],
            unclassified=totals["unclassified"],
        )
        if month_total == 0:
            # Dividing by zero to show "0%" everywhere would misrepresent an
            # empty month as a month with a (zero) spending mix.
            shares = None
        else:
            shares_raw = nature_shares(totals)
            shares = NatureShares(**{key: float(value) for key, value in shares_raw.items()})

        series.append(
            NatureMonth(
                month=month_start.strftime("%Y-%m"),
                trusted=month_start != current_month_start,
                income=month_totals[month_start]["income"],
                values=values,
                shares=shares,
            )
        )

    savings_accounts = (await session.scalars(
        select(Account).where(
            Account.workspace_id == workspace_id,
            Account.type == "savings",
            Account.is_closed.is_(False),
        )
    )).all()
    savings_destination = None
    if savings_accounts:
        start = month_starts[0]
        end = _month_range(month_starts[-1])[1]
        contribution_by_month = await _savings_contributions_by_month(
            session, workspace_id, primary_currency, accounting_mode, month_starts
        )
        amount = sum(contribution_by_month.values(), Decimal("0")).quantize(Decimal("0.01"))
        income_result = await session.execute(
            select(func.sum(_transaction_primary_amount(primary_currency)))
            .outerjoin(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.workspace_id == workspace_id,
                Transaction.type == "credit",
                date_col >= start,
                date_col < end,
                counts_as_user_pnl(),
                func.coalesce(Category.flow_type, "income") != "transfer",
            )
        )
        income = Decimal(str(income_result.scalar_one() or 0))
        savings_destination = SavingsDestination(
            amount=amount,
            share_of_income=float(amount / income) if income > 0 else None,
            account_count=len(savings_accounts),
        )

    return NatureData(series=series, reference=None, savings_destination=savings_destination)


def _shift_month(d: date, delta: int) -> date:
    """Return `d`'s month shifted by `delta` months, day fixed to 1."""
    total = (d.year * 12 + (d.month - 1)) + delta
    year, month0 = divmod(total, 12)
    return date(year, month0 + 1, 1)


# ---------------------------------------------------------------------------
# Breakeven table
# ---------------------------------------------------------------------------


def _ir_rate_for_installments(n: int) -> Decimal:
    """Brazilian IR bracket on fixed income by holding period, using the
    last installment's month (n months ~= n*30 days) as the holding period
    for the whole row."""
    days = n * 30
    for max_days, rate in _IR_BRACKETS:
        if days <= max_days:
            return rate
    return _IR_BEYOND


def _breakeven_net_gain(principal: Decimal, n: int, monthly_rate: Decimal, ir_rate: Decimal) -> Decimal:
    """Net gain (in `principal`'s unit) from keeping `principal` invested at
    gross `monthly_rate` while withdrawing `principal/n` at the end of each
    of `n` months, with IR charged once at redemption on the *total*
    accumulated gross interest earned across all months — not monthly, and
    not proportionally per withdrawal.

    This is the LOCKED tax model (see backend/app/services/insights_service.py
    module docstring / task brief): IR-monthly and IR-proportional-per-
    withdrawal are both wrong models that were explicitly rejected. Sanity
    check: R$ 5.000 in 12x at r=1,042%/month with IR 20% yields a net gain
    of R$ 292,45.
    """
    installment = principal / n
    balance = principal
    total_gross_interest = Decimal("0")
    for _ in range(n):
        interest = balance * monthly_rate
        balance += interest
        total_gross_interest += interest
        balance -= installment
    tax = total_gross_interest * ir_rate
    return total_gross_interest - tax


def get_breakeven_table(monthly_yield: float = DEFAULT_MONTHLY_YIELD) -> BreakevenTableData:
    """Static reference table: for a purchase split into N installments
    (N = 2..24), the cash discount that makes paying upfront equivalent to
    paying in installments, given a monthly gross yield rate."""
    rate = Decimal(str(monthly_yield))

    rows: list[BreakevenRow] = []
    for n in range(2, 25):
        ir_rate = _ir_rate_for_installments(n)
        gain_per_1000 = _breakeven_net_gain(Decimal("1000"), n, rate, ir_rate).quantize(Decimal("0.01"))
        breakeven_discount = float(gain_per_1000 / Decimal("1000"))
        net_monthly = rate * (1 - ir_rate)
        rows.append(
            BreakevenRow(
                n=n,
                ir_rate=float(ir_rate),
                net_monthly=f"{net_monthly:.5f}",
                breakeven_discount=breakeven_discount,
                gain_per_1000=gain_per_1000,
            )
        )

    yield_basis = YieldBasis(
        monthly_gross=f"{rate:.5f}",
        method="assumed",
        sample_size=0,
        window_days=YieldWindowDays(min=0, max=0),
        range=YieldRange(p25=f"{rate:.5f}", p75=f"{rate:.5f}"),
        as_of=date.today(),
    )
    return BreakevenTableData(yield_basis=yield_basis, rows=rows)


# ---------------------------------------------------------------------------
# Purchase decision
# ---------------------------------------------------------------------------


async def _available_cash(session: AsyncSession, workspace_id: uuid.UUID, primary_currency: str) -> Decimal:
    """Sum of balances across non-credit-card, non-closed accounts that
    hold spendable cash (checking, savings, wallet) — what the user could
    actually put toward an upfront payment."""
    result = await session.execute(
        select(Account.balance, Account.currency).where(
            Account.workspace_id == workspace_id,
            Account.is_closed.is_(False),
            Account.type.in_(("checking", "savings", "wallet")),
        )
    )
    total = Decimal("0")
    for balance, currency in result.all():
        converted, _ = await convert(session, Decimal(str(balance or 0)), currency, primary_currency)
        total += converted
    return total


async def _has_revolving_debt(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """True if any credit-card account currently carries a negative
    (owed) balance — i.e., debt beyond what's paid off, the situation in
    which the opportunity cost of cash is the card's revolving rate
    (13-15%/month in Brazil), not the ~1%/month CDB rate."""
    result = await session.execute(
        select(Account.balance).where(
            Account.workspace_id == workspace_id,
            Account.is_closed.is_(False),
            Account.type == "credit_card",
        )
    )
    return any((balance or Decimal("0")) < 0 for (balance,) in result.all())


# Typical Brazilian revolving credit-card interest rate, used as the
# opportunity cost of cash when the user carries revolving debt — far above
# the ~1%/month CDB yield, so paying cash essentially always wins.
_REVOLVING_MONTHLY_RATE = Decimal("0.14")


async def get_purchase_decision(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    request: PurchaseDecisionRequest,
) -> PurchaseDecisionData:
    """Decide upfront vs. installments for a purchase, checking two
    preconditions before any verdict is computed:

    1. Available cash: if the user can't cover `request.price` from
       checking/savings/wallet balances, upfront isn't an option regardless
       of the math.
    2. Revolving debt: if the user carries revolving credit-card debt, the
       opportunity cost of cash is the card's rate (~13-15%/month), not the
       CDB's ~1%/month — under revolving debt, paying cash essentially
       always wins.
    """
    price = request.price
    n = request.installments
    cash_discount_pct = Decimal(str(request.cash_discount_pct))

    available_cash = await _available_cash(session, workspace_id, primary_currency)
    has_debt = await _has_revolving_debt(session, workspace_id)
    can_pay_upfront = available_cash >= price

    if has_debt:
        # Opportunity cost is the revolving rate, not the CDB yield: paying
        # cash to avoid installments (which sit on top of, or delay paying
        # down, debt compounding at ~14%/month) dominates any breakeven math.
        monthly_rate = _REVOLVING_MONTHLY_RATE
    else:
        monthly_rate = Decimal(str(request.yield_override)) if request.yield_override else Decimal(str(DEFAULT_MONTHLY_YIELD))

    if not can_pay_upfront:
        choice = "installments"
        net_gain = Decimal("0.00")
        breakeven_discount = 0.0
        headline = (
            f"Saldo disponível insuficiente para pagar à vista "
            f"(faltam {price - available_cash:.2f})."
        )
    elif has_debt:
        choice = "cash"
        net_gain = (price * _REVOLVING_MONTHLY_RATE).quantize(Decimal("0.01"))
        breakeven_discount = 0.0
        headline = "Há dívida rotativa no cartão: pagar à vista evita juros do rotativo, muito acima do rendimento do CDB."
    else:
        ir_rate = _ir_rate_for_installments(n)
        # Investment gain from paying in installments (money stays invested,
        # withdrawn monthly) vs. the cash value given up by not taking the
        # upfront discount.
        installments_gain = _breakeven_net_gain(price, n, monthly_rate, ir_rate).quantize(Decimal("0.01"))
        cash_discount_value = (price * cash_discount_pct).quantize(Decimal("0.01"))
        breakeven_discount = float(installments_gain / price) if price else 0.0

        if cash_discount_value >= installments_gain:
            choice = "cash"
            net_gain = (cash_discount_value - installments_gain).quantize(Decimal("0.01"))
            headline = (
                f"Desconto à vista ({float(cash_discount_pct) * 100:.2f}%) supera "
                f"o ganho de investir o valor e pagar parcelado."
            )
        else:
            choice = "installments"
            net_gain = (installments_gain - cash_discount_value).quantize(Decimal("0.01"))
            headline = (
                f"Investir o valor e parcelar rende mais do que o desconto à vista "
                f"oferecido ({float(cash_discount_pct) * 100:.2f}%)."
            )

    schedule: list[PurchaseDecisionScheduleRow] = []
    if not has_debt and can_pay_upfront:
        installment_amount = (price / n).quantize(Decimal("0.01"))
        balance = price
        cumulative_gain = Decimal("0.00")
        for month in range(1, n + 1):
            interest = balance * monthly_rate
            balance += interest
            yield_net = (interest * (1 - _ir_rate_for_installments(n))).quantize(Decimal("0.01"))
            cumulative_gain += yield_net
            balance -= installment_amount
            schedule.append(
                PurchaseDecisionScheduleRow(
                    month=month,
                    payment=installment_amount,
                    balance_invested=balance.quantize(Decimal("0.01")),
                    yield_net=yield_net,
                    cumulative_gain=cumulative_gain.quantize(Decimal("0.01")),
                )
            )

    flexibility_impact = FlexibilityImpact(
        utilization_before=0.0,
        utilization_after=0.0,
        monthly_commitment_delta=[],
        cash_gap_month=None,
    )

    return PurchaseDecisionData(
        verdict=PurchaseDecisionVerdict(
            choice=choice,
            net_gain=net_gain,
            breakeven_discount=breakeven_discount,
            headline=headline,
        ),
        schedule=schedule,
        flexibility_impact=flexibility_impact,
    )


# ---------------------------------------------------------------------------
# Shared: monthly income / consumption / installments aggregation
#
# Vitals, flow, projection, goals and alerts all need "how much came in /
# went out per month" sliced a few different ways. Centralized here so the
# income definition (counts_as_user_pnl + reporting_date_col) is applied
# identically everywhere rather than re-derived per endpoint.
# ---------------------------------------------------------------------------


async def _monthly_totals(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    accounting_mode: str,
    month_starts: list[date],
) -> dict[date, dict[str, Decimal]]:
    """For each month in `month_starts`, return
    {"income": Decimal, "consumption": Decimal} in primary currency.

    "income" is credit-side counts_as_user_pnl (Category.flow_type=='income'
    when set, else any credit that counts as P&L — flow_type is still being
    backfilled, so we don't hard-require it).
    "consumption" is debit-side counts_as_user_pnl restricted to
    flow_type='consumption' categories (or uncategorized), matching
    get_nature's convention.
    """
    date_col = reporting_date_col(accounting_mode)
    out: dict[date, dict[str, Decimal]] = {
        m: {"income": Decimal("0"), "consumption": Decimal("0")} for m in month_starts
    }
    if not month_starts:
        return out

    window_start = min(month_starts)
    window_end = _month_range(max(month_starts))[1]

    result = await session.execute(
        select(
            date_col.label("reported_on"),
            Transaction.type,
            Category.flow_type,
            func.sum(_transaction_primary_amount(primary_currency)).label("total"),
        )
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            date_col >= window_start,
            date_col < window_end,
            counts_as_user_pnl(),
        )
        .group_by(date_col, Transaction.type, Category.flow_type)
    )

    for reported_on, tx_type, flow_type, total in result.all():
        month_start = reported_on.replace(day=1)
        if month_start not in out:
            continue
        if tx_type == "credit" and flow_type != "transfer":
            out[month_start]["income"] += Decimal(str(total or 0))
        elif tx_type == "debit" and flow_type in (None, "consumption"):
            out[month_start]["consumption"] += Decimal(str(total or 0))

    return out


async def _installment_monthly_totals(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    accounting_mode: str,
    month_starts: list[date],
) -> dict[date, Decimal]:
    """Sum of installment rows landing in each month, in primary currency.

    `installment_series_id` is 100% NULL in real data, so installments are
    grouped by (description, installment_total_amount, total_installments)
    instead of by series id — that triple identifies "the same purchase's
    installment plan" well enough for a monthly sum, which doesn't need the
    individual row identity a plan *view* would.
    """
    date_col = reporting_date_col(accounting_mode)
    out: dict[date, Decimal] = {m: Decimal("0") for m in month_starts}
    if not month_starts:
        return out

    window_start = min(month_starts)
    window_end = _month_range(max(month_starts))[1]

    result = await session.execute(
        select(
            date_col.label("reported_on"),
            _transaction_primary_amount(primary_currency).label("amount_primary_value"),
        )
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.total_installments.is_not(None),
            Transaction.type == "debit",
            date_col >= window_start,
            date_col < window_end,
            counts_as_user_pnl(),
        )
    )
    for reported_on, amount in result.all():
        month_start = reported_on.replace(day=1)
        if month_start not in out:
            continue
        out[month_start] += Decimal(str(amount or 0))

    return out


async def _recurring_monthly_totals(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    month_starts: list[date],
) -> dict[date, Decimal]:
    """Net active recurring cash flow for each projected month.

    Reuse dashboard's read-only occurrence generator so materialized rows are
    not counted twice. Transfer-like and ignored recurring rules stay out of
    P&L, matching the other Insights aggregations.
    """
    out: dict[date, Decimal] = {m: Decimal("0") for m in month_starts}
    for month_start in month_starts:
        projections = await _get_recurring_projections(
            session,
            workspace_id,
            month_start,
            _month_range(month_start)[1],
        )
        for projection in projections:
            converted, _ = await convert(
                session,
                Decimal(str(projection["amount"])),
                projection["currency"],
                primary_currency,
            )
            out[month_start] += converted if projection["type"] == "credit" else -converted
    return out


def _trailing_month_starts(today: date, n: int, *, include_current: bool = False) -> list[date]:
    """Oldest-to-newest list of `n` month-starts ending at (or just before)
    the current month."""
    current_month_start = _month_start(today)
    end_cursor = current_month_start if include_current else _shift_month(current_month_start, -1)
    starts = [_shift_month(end_cursor, -i) for i in range(n)]
    starts.reverse()
    return starts


def standard_insights_window(today: date | None = None) -> InsightsWindow:
    """Common report window exposed by every diagnostic block."""
    today = today or date.today()
    current = _month_start(today)
    return InsightsWindow(
        **{"from": _shift_month(current, -11), "to": today},
        trusted_from=_shift_month(current, -6),
        months_trusted=6,
    )


def _trend_summary(values: list[Decimal], *, favorable_up: bool, baseline: Decimal | None) -> VitalTrend | None:
    """Classify direction once on server; UI only renders it."""
    if len(values) < 2:
        return None
    first, last = values[0], values[-1]
    delta = (last - first).quantize(Decimal("0.01"))
    scale = max(abs(first), abs(last), Decimal("1"))
    relative = abs(delta) / scale
    if relative < Decimal("0.03"):
        direction: Literal["up", "down", "stable"] = "stable"
        intensity: Literal["strong", "light", "stable"] = "stable"
        label = "estável"
        favorable = None
    else:
        direction = "up" if delta > 0 else "down"
        intensity = "strong" if relative >= Decimal("0.15") else "light"
        favorable = direction == "up" if favorable_up else direction == "down"
        label = "melhorando" if favorable else "piorando"
    return VitalTrend(
        direction=direction,
        intensity=intensity,
        delta=delta,
        baseline=baseline.quantize(Decimal("0.01")) if baseline is not None else None,
        favorable=favorable,
        label=label,
    )


async def _savings_contributions_by_month(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    accounting_mode: str,
    month_starts: list[date],
) -> dict[date, Decimal]:
    """Net only confirmed internal transfers into savings accounts.

    External credits and unpaired rows are intentionally excluded.
    """
    out = {month: Decimal("0") for month in month_starts}
    if not month_starts:
        return out
    savings = (await session.scalars(select(Account).where(
        Account.workspace_id == workspace_id,
        Account.type == "savings",
        Account.is_closed.is_(False),
    ))).all()
    if not savings:
        return out
    date_col = reporting_date_col(accounting_mode)
    result = await session.execute(
        select(Transaction, Account.type)
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.account_id.in_([account.id for account in savings]),
            Transaction.transfer_pair_id.is_not(None),
            date_col >= month_starts[0],
            date_col < _month_range(month_starts[-1])[1],
        )
    )
    for transaction, account_type in result.all():
        if account_type != "savings":
            continue
        reported_date = transaction.effective_bill_date or transaction.effective_date or transaction.date
        month = reported_date.replace(day=1)
        if month not in out:
            continue
        amount = transaction.amount_primary if transaction.currency == primary_currency else transaction.amount_primary
        if amount is None:
            amount, _ = await convert(session, Decimal(str(transaction.amount)), transaction.currency, primary_currency)
        else:
            amount = Decimal(str(amount))
        out[month] += amount if transaction.type == "credit" else -amount
    return out



# ---------------------------------------------------------------------------
# Vitals
# ---------------------------------------------------------------------------


async def get_vitals(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
) -> list[VitalCard]:
    """Four cards: runway, savings_rate, net_worth, credit_utilization."""
    accounting_mode = await get_credit_card_accounting_mode(session)
    today = date.today()

    cards = [
        await _runway_card(session, workspace_id, primary_currency, accounting_mode, today),
        await _savings_rate_card(session, workspace_id, primary_currency, accounting_mode, today),
        await _net_worth_card(session, workspace_id, primary_currency, today),
        await _credit_utilization_card(session, workspace_id, primary_currency),
    ]
    return cards


async def _essential_monthly_cost(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    accounting_mode: str,
    today: date,
) -> tuple[Optional[Decimal], int]:
    """Essential monthly cost = median(fixed + variable consumption) over
    trusted trailing months, plus the peak installment month over the next
    6 months (installments are legally owed — in an emergency you cut Uber,
    not instalment 7/12).

    Returns (cost, months_used). cost is None when months_used is below
    MIN_MONTHS_FOR_RUNWAY.
    """
    trailing = _trailing_month_starts(today, 6, include_current=False)
    date_col = reporting_date_col(accounting_mode)
    effective_nature = func.coalesce(Transaction.expense_nature, Category.expense_nature)

    window_start = trailing[0]
    window_end = _month_range(trailing[-1])[1]

    result = await session.execute(
        select(
            date_col.label("reported_on"),
            effective_nature.label("nature"),
            func.sum(_transaction_primary_amount(primary_currency)).label("total"),
        )
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.type == "debit",
            Category.flow_type == "consumption",
            date_col >= window_start,
            date_col < window_end,
            counts_as_user_pnl(),
        )
        .group_by(date_col, effective_nature)
    )

    per_month: dict[date, Decimal] = {m: Decimal("0") for m in trailing}
    months_with_activity: set[date] = set()
    for reported_on, nature, total in result.all():
        month_start = reported_on.replace(day=1)
        if month_start not in per_month:
            continue
        if nature not in ("fixed", "variable"):
            continue
        per_month[month_start] += Decimal(str(total or 0))
        months_with_activity.add(month_start)

    # A trusted month is one with at least some P&L activity recorded — an
    # empty month (no transactions synced/entered yet) isn't a real zero,
    # it's missing data, and would drag the median down artificially.
    months_used = len(months_with_activity)
    if months_used < MIN_MONTHS_FOR_RUNWAY:
        return None, months_used

    values = [float(per_month[m]) for m in trailing if m in months_with_activity]
    base_cost = Decimal(str(statistics.median(values))).quantize(Decimal("0.01"))

    # Peak installment month over the next 6 months (forward-looking:
    # installments already committed to, regardless of past history).
    future_months = [_shift_month(_month_start(today), i) for i in range(0, 6)]
    installment_totals = await _installment_monthly_totals(
        session, workspace_id, primary_currency, accounting_mode, future_months
    )
    peak_installments = max(installment_totals.values(), default=Decimal("0"))

    return base_cost + peak_installments, months_used


async def _emergency_fund_balance(
    session: AsyncSession, workspace_id: uuid.UUID, primary_currency: str
) -> Optional[Decimal]:
    result = await session.execute(
        select(Account).where(
            Account.workspace_id == workspace_id,
            Account.type == "savings",
            Account.is_closed.is_(False),
        )
    )
    accounts = result.scalars().all()
    if not accounts:
        return None
    total = Decimal("0")
    for account in accounts:
        bal = await _account_balance_at(session, account, date.today())
        converted, _ = await convert(session, Decimal(str(bal)), account.currency, primary_currency)
        total += converted
    return total


async def _runway_card(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    accounting_mode: str,
    today: date,
) -> VitalCard:
    fund_balance = await _emergency_fund_balance(session, workspace_id, primary_currency)
    if fund_balance is None:
        return VitalCard(
            key="runway",
            label="Fôlego financeiro",
            value=None,
            unit="months",
            reference=None,
            status="unknown",
            available=False,
            blocked_reason="Conta de reserva de emergência não encontrada.",
            series=None,
        )

    essential_cost, months_used = await _essential_monthly_cost(
        session, workspace_id, primary_currency, accounting_mode, today
    )
    if essential_cost is None:
        return VitalCard(
            key="runway",
            label="Fôlego financeiro",
            value=None,
            unit="months",
            reference=None,
            status="unknown",
            available=False,
            blocked_reason=(
                f"Histórico insuficiente para estimar o custo essencial: "
                f"{months_used} de {MIN_MONTHS_FOR_RUNWAY} meses confiáveis disponíveis."
            ),
            series=None,
        )

    if essential_cost <= 0:
        return VitalCard(
            key="runway",
            label="Fôlego financeiro",
            value=None,
            unit="months",
            reference=None,
            status="unknown",
            available=False,
            blocked_reason="Custo essencial mensal calculado como zero.",
            series=None,
        )

    months = (fund_balance / essential_cost).quantize(Decimal("0.1"))
    projection = await get_projection(session, workspace_id, primary_currency)
    projected_balances = [point.balance for point in projection.points if point.kind == "projected" and point.balance is not None]
    minimum_projected = min(projected_balances, default=Decimal("0"))
    status_name = security_status(months, minimum_projected, essential_cost)
    status: Literal["good", "warn", "crit", "unknown"] = {
        "safe": "good", "attention": "warn", "risk": "crit"
    }[status_name]

    trend_points: list[VitalSeriesPoint] = []
    for month in _trailing_month_starts(today, 12, include_current=False):
        month_end = _month_range(month)[1] - timedelta(days=1)
        balance = Decimal("0")
        savings_accounts = (await session.scalars(select(Account).where(
            Account.workspace_id == workspace_id,
            Account.type == "savings",
            Account.is_closed.is_(False),
        ))).all()
        for account in savings_accounts:
            raw = await _account_balance_at(session, account, month_end)
            converted, _ = await convert(session, Decimal(str(raw)), account.currency, primary_currency)
            balance += converted
        trend_points.append(VitalSeriesPoint(
            date=month.isoformat(), value=(balance / essential_cost).quantize(Decimal("0.01")), trusted=True
        ))

    return VitalCard(
        key="runway",
        label="Segurança financeira",
        value=months,
        unit="months",
        reference=VitalReference(
            value=essential_cost,
            source="historical",
            method="median_fixed_variable_plus_peak_installment",
            label="Custo essencial mensal (mediana + pico de parcelas em 6 meses)",
        ),
        status=status,
        available=True,
        blocked_reason=(
            f"Piso projetado em 12 meses: R$ {minimum_projected:.2f}."
            if minimum_projected < essential_cost else None
        ),
        series=trend_points,
        trend=_trend_summary(
            [point.value for point in trend_points], favorable_up=True, baseline=Decimal("6")
        ),
    )


async def _savings_rate_card(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    accounting_mode: str,
    today: date,
) -> VitalCard:
    """Savings routed to any savings account divided by external inflows."""
    month_starts = _trailing_month_starts(today, 6, include_current=True)
    totals = await _monthly_totals(
        session, workspace_id, primary_currency, accounting_mode, month_starts
    )
    income = sum((totals[month]["income"] for month in month_starts), Decimal("0"))
    contribution_by_month = await _savings_contributions_by_month(
        session, workspace_id, primary_currency, accounting_mode, month_starts
    )
    contribution = sum(contribution_by_month.values(), Decimal("0"))

    if income <= 0:
        return VitalCard(
            key="savings_rate",
            label="Taxa de poupança",
            value=None,
            unit="percent",
            reference=None,
            status="unknown",
            available=False,
            blocked_reason="Sem renda registrada no mês.",
            series=None,
            trend=None,
        )

    rate = savings_rate(contribution, income)
    if rate >= 20:
        status: Literal["good", "warn", "crit", "unknown"] = "good"
    elif rate >= 0:
        status = "warn"
    else:
        status = "crit"

    series = []
    for month in month_starts:
        month_income = totals[month]["income"]
        month_contribution = contribution_by_month[month]
        month_rate = savings_rate(month_contribution, month_income) if month_income > 0 else Decimal("0")
        series.append(VitalSeriesPoint(date=month.isoformat(), value=month_rate, trusted=month != _month_start(today)))

    return VitalCard(
        key="savings_rate",
        label="Taxa de poupança",
        value=rate,
        unit="percent",
        reference=VitalReference(
            value=contribution.quantize(Decimal("0.01")),
            source="historical",
            method="paired_savings_transfers_over_external_inflows_rolling_6_months",
            label="Aportes líquidos em savings / entradas externas (6 meses)",
        ),
        status=status,
        available=True,
        blocked_reason=None,
        series=series,
        trend=_trend_summary([point.value for point in series], favorable_up=True, baseline=Decimal("20")),
    )


async def _net_worth_card(
    session: AsyncSession, workspace_id: uuid.UUID, primary_currency: str, today: date
) -> VitalCard:
    """Mirrors report_service._net_worth_at's convention exactly
    (accounts_total + assets_total - liabilities_total, liability = credit
    card or negative balance, taken as abs), with one deliberate exception:
    the "Reserva de Emergência" account is excluded, because it mirrors the
    Pluggy CDBs already counted in assets_total and counting both double-
    counts the same money. This makes this tab's net worth differ from the
    native Reports screen BY DESIGN — that's why the label says so."""
    accounts = await _get_open_accounts(session, workspace_id, None)

    accounts_total = Decimal("0")
    liabilities_total = Decimal("0")
    for account in accounts:
        if account.name == _EMERGENCY_FUND_ACCOUNT_NAME:
            continue
        bal = await _account_balance_at(session, account, today)
        converted, _ = await convert(
            session, Decimal(str(abs(bal))), account.currency, primary_currency
        )
        if account.type == "credit_card" or bal < 0:
            liabilities_total += converted
        else:
            accounts_total += converted

    assets_total = Decimal("0")
    asset_result = await session.execute(
        select(Asset).where(
            Asset.workspace_id == workspace_id,
            Asset.is_archived.is_(False),
            Asset.sell_date.is_(None),
        )
    )
    for asset in asset_result.scalars().all():
        val_result = await session.execute(
            select(AssetValue.amount)
            .where(AssetValue.asset_id == asset.id, AssetValue.date <= today)
            .order_by(AssetValue.date.desc(), AssetValue.id.desc())
            .limit(1)
        )
        val = val_result.scalar_one_or_none()
        if val is not None:
            amount = Decimal(str(val))
        elif asset.purchase_price is not None and (
            asset.purchase_date is None or asset.purchase_date <= today
        ):
            amount = asset.purchase_price
        else:
            amount = Decimal("0")
        if amount > 0:
            converted, _ = await convert(session, amount, asset.currency, primary_currency)
            assets_total += converted

    net_worth = accounts_total + assets_total - liabilities_total

    trend_points: list[VitalSeriesPoint] = []
    for month in _trailing_month_starts(today, 12, include_current=False):
        month_balance = await _total_open_account_balance(
            session, workspace_id, primary_currency, _month_range(month)[1] - timedelta(days=1)
        )
        trend_points.append(VitalSeriesPoint(date=month.isoformat(), value=month_balance, trusted=True))

    return VitalCard(
        key="net_worth",
        label="Patrimônio líquido",
        value=net_worth.quantize(Decimal("0.01")),
        unit="BRL",
        reference=None,
        status="good" if net_worth >= 0 else "warn",
        available=True,
        blocked_reason=(
            "Exclui a conta \"Reserva de Emergência\": ela espelha as CDBs do "
            "Pluggy já somadas em ativos, então somar as duas contaria o "
            "mesmo dinheiro duas vezes. Por isso este número difere do valor "
            "mostrado nas telas de Relatórios."
        ),
        series=trend_points,
        trend=_trend_summary([point.value for point in trend_points], favorable_up=True, baseline=None),
    )


async def _credit_utilization_card(
    session: AsyncSession, workspace_id: uuid.UUID, primary_currency: str,
    today: date | None = None,
) -> VitalCard:
    today = today or date.today()
    result = await session.execute(
        select(Account).where(
            Account.workspace_id == workspace_id,
            Account.is_closed.is_(False),
            Account.type == "credit_card",
        )
    )
    accounts = result.scalars().all()

    limit_total = Decimal("0")
    utilized_total = Decimal("0")
    has_limit_data = False
    for account in accounts:
        if account.credit_limit is None:
            continue
        has_limit_data = True
        current_balance = await _account_balance_at(session, account, today)
        available = compute_available_credit(account.credit_limit, Decimal(str(current_balance)))
        utilized = account.credit_limit - (available if available is not None else account.credit_limit)
        limit_c, _ = await convert(session, account.credit_limit, account.currency, primary_currency)
        util_c, _ = await convert(session, utilized, account.currency, primary_currency)
        limit_total += limit_c
        utilized_total += util_c

    if not accounts:
        return VitalCard(
            key="credit_utilization",
            label="Utilização do crédito",
            value=None,
            unit="percent",
            reference=None,
            status="unknown",
            available=False,
            blocked_reason="Nenhum cartão de crédito cadastrado.",
            series=None,
        )
    if not has_limit_data or limit_total <= 0:
        return VitalCard(
            key="credit_utilization",
            label="Utilização do crédito",
            value=None,
            unit="percent",
            reference=None,
            status="unknown",
            available=False,
            blocked_reason="Limite de crédito não informado para os cartões.",
            series=None,
        )

    ratio = float(utilized_total / limit_total)
    if ratio <= 0.30:
        status: Literal["good", "warn", "crit", "unknown"] = "good"
    elif ratio <= 0.70:
        status = "warn"
    else:
        status = "crit"

    trend_points: list[VitalSeriesPoint] = []
    for month in _trailing_month_starts(today, 12, include_current=False):
        month_limit = Decimal("0")
        month_used = Decimal("0")
        for account in accounts:
            if account.credit_limit is None:
                continue
            balance = await _account_balance_at(session, account, _month_range(month)[1] - timedelta(days=1))
            available = compute_available_credit(account.credit_limit, Decimal(str(balance)))
            used = account.credit_limit - (available if available is not None else account.credit_limit)
            limit_c, _ = await convert(session, account.credit_limit, account.currency, primary_currency)
            used_c, _ = await convert(session, used, account.currency, primary_currency)
            month_limit += limit_c
            month_used += used_c
        if month_limit > 0:
            trend_points.append(VitalSeriesPoint(
                date=month.isoformat(), value=(month_used / month_limit * 100).quantize(Decimal("0.01")), trusted=True
            ))

    return VitalCard(
        key="credit_utilization",
        label="Utilização do crédito",
        value=Decimal(str(round(ratio * 100, 2))),
        unit="percent",
        reference=None,
        status=status,
        available=True,
        blocked_reason=None,
        series=trend_points,
        trend=_trend_summary([point.value for point in trend_points], favorable_up=False, baseline=Decimal("30")),
    )


# ---------------------------------------------------------------------------
# Flow (Sankey)
# ---------------------------------------------------------------------------

# Categories below this share of income collapse into "Outros" (per group).
_FLOW_COLLAPSE_THRESHOLD_PCT = Decimal("0.03")


async def get_flow(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    month: Optional[date] = None,
) -> FlowData:
    """Sankey: income -> category groups -> categories, plus a "Poupado"
    node. saldo = renda - gastos - poupado. If negative, the source node
    becomes "renda + deficit" and deficit is reported separately.

    Invariant that must hold (and is asserted in tests): for every node,
    the sum of its outgoing edges equals the node's value, within a cent.
    """
    accounting_mode = await get_credit_card_accounting_mode(session)
    date_col = reporting_date_col(accounting_mode)
    target_month = _month_start(month or date.today())
    next_month = _add_month(target_month)

    income_result = await session.execute(
        select(func.sum(_transaction_primary_amount(primary_currency)))
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.type == "credit",
            date_col >= target_month,
            date_col < next_month,
            counts_as_user_pnl(),
            func.coalesce(Category.flow_type, "income") != "transfer",
        )
    )
    income_total = Decimal("0")
    income_total += Decimal(str(income_result.scalar_one() or 0))

    spend_result = await session.execute(
        select(
            Category.id,
            Category.name,
            Category.color,
            Category.group_id,
            func.sum(_transaction_primary_amount(primary_currency)).label("total"),
        )
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.type == "debit",
            Category.flow_type == "consumption",
            date_col >= target_month,
            date_col < next_month,
            counts_as_user_pnl(),
        )
        .group_by(Category.id, Category.name, Category.color, Category.group_id)
    )
    rows = spend_result.all()

    per_category: dict[uuid.UUID, dict] = {}
    for cat_id, name, color, group_id, total in rows:
        bucket = per_category.setdefault(
            cat_id, {"name": name, "color": color, "group_id": group_id, "amount": Decimal("0")}
        )
        bucket["amount"] += Decimal(str(total or 0))

    group_meta = await _category_group_meta(session, workspace_id)

    saved_result = await session.execute(
        select(func.sum(_transaction_primary_amount(primary_currency)))
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.type == "debit",
            Category.flow_type == "saving",
            date_col >= target_month,
            date_col < next_month,
            counts_as_user_pnl(),
        )
    )
    saved_total = Decimal(str(saved_result.scalar_one() or 0))

    gastos_total = sum((b["amount"] for b in per_category.values()), Decimal("0"))
    saldo = income_total - gastos_total - saved_total

    deficit = None
    source_value = income_total
    if saldo < 0:
        deficit = -saldo
        source_value = income_total + deficit
        saldo = Decimal("0")

    if income_total == 0 and gastos_total == 0 and saved_total == 0:
        return FlowData(nodes=[], links=[], collapse_threshold=Decimal("0.00"), income_total=None, deficit=None)

    nodes: list[FlowNode] = [
        FlowNode(id="renda", label="Renda", depth=0, kind="income", color="#22C55E", value=source_value)
    ]
    links: list[FlowLink] = []

    # Group categories under their category-group; collapse below-threshold
    # categories within each group into "Outros".
    collapse_floor = (income_total * _FLOW_COLLAPSE_THRESHOLD_PCT) if income_total > 0 else Decimal("0")
    by_group: dict[Optional[uuid.UUID], list] = {}
    for cat_id, data in per_category.items():
        by_group.setdefault(data["group_id"], []).append((cat_id, data))

    collapsed_total = Decimal("0")
    for group_id, cats in by_group.items():
        meta = group_meta.get(group_id, {"name": "Sem grupo", "color": "#6B7280"})
        group_node_id = f"group:{group_id}" if group_id else "group:none"
        group_value = sum((d["amount"] for _cid, d in cats), Decimal("0"))
        nodes.append(
            FlowNode(id=group_node_id, label=meta["name"], depth=1, kind="group", color=meta["color"], value=group_value, full_path=meta["name"])
        )
        links.append(FlowLink(source="renda", target=group_node_id, value=group_value))

        kept_total = Decimal("0")
        group_collapsed = Decimal("0")
        for cat_id, data in cats:
            if data["amount"] < collapse_floor:
                group_collapsed += data["amount"]
                collapsed_total += data["amount"]
                continue
            kept_total += data["amount"]
            cat_node_id = f"cat:{cat_id}"
            nodes.append(
                FlowNode(
                    id=cat_node_id, label=data["name"], depth=2, kind="category",
                    color=data["color"], value=data["amount"], full_path=f"{meta['name']} › {data['name']}",
                )
            )
            links.append(FlowLink(source=group_node_id, target=cat_node_id, value=data["amount"]))

        if group_collapsed > 0:
            other_node_id = f"{group_node_id}:outros"
            nodes.append(
                FlowNode(id=other_node_id, label="Outros", depth=2, kind="category", color="#9CA3AF", value=group_collapsed, full_path=f"{meta['name']} › Outros")
            )
            links.append(FlowLink(source=group_node_id, target=other_node_id, value=group_collapsed))

    if saved_total > 0:
        nodes.append(
            FlowNode(id="poupado", label="Poupado", depth=1, kind="saved", color="#0EA5E9", value=saved_total)
        )
        links.append(FlowLink(source="renda", target="poupado", value=saved_total))

    if saldo > 0:
        nodes.append(
            FlowNode(id="saldo", label="Saldo", depth=1, kind="saved", color="#A3E635", value=saldo)
        )
        links.append(FlowLink(source="renda", target="saldo", value=saldo))

    if deficit is not None and deficit > 0:
        nodes.append(
            FlowNode(id="deficit", label="Déficit", depth=1, kind="saved", color="#EF4444", value=deficit)
        )
        links.append(FlowLink(source="renda", target="deficit", value=deficit))

    return FlowData(
        nodes=nodes,
        links=links,
        collapse_threshold=collapsed_total.quantize(Decimal("0.01")),
        income_total=income_total.quantize(Decimal("0.01")),
        deficit=deficit.quantize(Decimal("0.01")) if deficit is not None else None,
    )


async def _category_group_meta(session: AsyncSession, workspace_id: uuid.UUID) -> dict:
    result = await session.execute(
        select(CategoryGroup).where(CategoryGroup.workspace_id == workspace_id)
    )
    return {g.id: {"name": g.name, "color": g.color} for g in result.scalars().all()}


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

# How many months forward the projection runs.
_PROJECTION_HORIZON_MONTHS = 12
# How many trailing months of actuals feed the p25/p75 variable-spend band.
_PROJECTION_HISTORY_MONTHS = 6


async def get_projection(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
) -> ProjectionData:
    """Balance projection. The confidence band's half-width scales with
    sqrt(n), not linearly: hw(n) = hw(1) * sqrt(n), where hw(1) is half the
    p75-p25 spread of monthly spend. A linear band would be 3.3x too wide
    at 12 months (assumes perfectly correlated monthly errors), wide enough
    that the pessimistic case crosses zero almost always and disarms the
    calculator's sanity check.

    Known installments are fact, not estimate: kept separately in
    components.installments / committed, never folded into the variable
    estimate or the uncertainty band.
    """
    accounting_mode = await get_credit_card_accounting_mode(session)
    today = date.today()

    history_months = _trailing_month_starts(today, _PROJECTION_HISTORY_MONTHS, include_current=False)
    history_totals = await _monthly_totals(
        session, workspace_id, primary_currency, accounting_mode, history_months
    )
    history_income = [history_totals[m]["income"] for m in history_months if history_totals[m]["income"] > 0]
    history_consumption = [history_totals[m]["consumption"] for m in history_months]

    # Residual histories keep recurring rules and known installments from
    # being counted again as variable estimates.
    date_col = reporting_date_col(accounting_mode)
    residual_result = await session.execute(
        select(
            date_col.label("reported_on"), Transaction.type,
            func.sum(_transaction_primary_amount(primary_currency)).label("total"),
        ).where(
            Transaction.workspace_id == workspace_id,
            date_col >= history_months[0],
            date_col < _month_range(history_months[-1])[1],
            counts_as_user_pnl(),
            Transaction.recurring_transaction_id.is_(None),
            Transaction.total_installments.is_(None),
        ).group_by(date_col, Transaction.type)
    )
    residual_by_month = {month: {"income": Decimal("0"), "consumption": Decimal("0")} for month in history_months}
    for reported_on, tx_type, total in residual_result.all():
        month = reported_on.replace(day=1)
        if month not in residual_by_month:
            continue
        if tx_type == "credit":
            residual_by_month[month]["income"] += Decimal(str(total or 0))
        elif tx_type == "debit":
            residual_by_month[month]["consumption"] += Decimal(str(total or 0))

    residual_income = [residual_by_month[m]["income"] for m in history_months if residual_by_month[m]["income"] > 0]
    residual_consumption = [residual_by_month[m]["consumption"] for m in history_months]
    income_variable_estimate = (
        Decimal(str(statistics.median([float(v) for v in residual_income]))).quantize(Decimal("0.01"))
        if len(residual_income) >= 2 else None
    )
    variable_values = [float(v) for v in residual_consumption]
    if len(variable_values) >= 2:
        p25 = Decimal(str(statistics.quantiles(variable_values, n=4, method="inclusive")[0]))
        p75 = Decimal(str(statistics.quantiles(variable_values, n=4, method="inclusive")[2]))
    elif variable_values:
        p25 = p75 = Decimal(str(variable_values[0]))
    else:
        p25 = p75 = Decimal("0")
    expense_variable_estimate = (
        Decimal(str(statistics.median(variable_values))).quantize(Decimal("0.01"))
        if variable_values
        else None
    )
    half_width_1 = ((p75 - p25) / 2).quantize(Decimal("0.01"))

    projection_schedule = projection_months(today)
    actual_months = [date.fromisoformat(f"{month}-01") for month, kind in projection_schedule if kind == "actual"]
    future_months = [date.fromisoformat(f"{month}-01") for month, kind in projection_schedule if kind == "projected"]
    installment_totals = await _installment_monthly_totals(
        session, workspace_id, primary_currency, accounting_mode, future_months
    )
    recurring_income: dict[date, Decimal] = {month: Decimal("0") for month in future_months}
    recurring_expense: dict[date, Decimal] = {month: Decimal("0") for month in future_months}
    for month in future_months:
        projections = await _get_recurring_projections(
            session, workspace_id, month, _month_range(month)[1]
        )
        for projection in projections:
            converted, _ = await convert(
                session, Decimal(str(projection["amount"])), projection["currency"], primary_currency
            )
            if projection["type"] == "credit":
                recurring_income[month] += converted
            else:
                recurring_expense[month] += converted

    current_balance = await _total_open_account_balance(session, workspace_id, primary_currency, today)

    points: list[ProjectionPoint] = []
    for month_start in actual_months:
        month_end = _month_range(month_start)[1]
        balance = current_balance if month_start == _month_start(today) else await _total_open_account_balance(
            session, workspace_id, primary_currency, month_end - timedelta(days=1)
        )
        points.append(
            ProjectionPoint(
                month=month_start.strftime("%Y-%m"), kind="actual",
                balance=balance.quantize(Decimal("0.01")), low=None, high=None,
                committed=Decimal("0.00"),
                components=ProjectionComponents(
                    income_recurring=None, income_variable_estimate=None,
                    expense_recurring=None, expense_variable_estimate=None,
                    installments_known=Decimal("0.00"),
                    component_sources={}, component_windows={},
                    income_expected=Decimal("0.00"), recurring=Decimal("0.00"),
                    installments=Decimal("0.00"), variable_estimate=Decimal("0.00"),
                ),
            )
        )

    running_balance = current_balance
    for n, month_start in enumerate(future_months, start=1):
        installments = installment_totals.get(month_start, Decimal("0"))
        income_recurring = recurring_income.get(month_start, Decimal("0"))
        expense_recurring = recurring_expense.get(month_start, Decimal("0"))
        recurring = income_recurring - expense_recurring
        committed = installments
        variable_income = income_variable_estimate or Decimal("0")
        variable_expense = expense_variable_estimate or Decimal("0")
        net = income_recurring + variable_income - expense_recurring - variable_expense - installments
        running_balance = running_balance + net
        half_width = (half_width_1 * Decimal(str(math.sqrt(n)))).quantize(Decimal("0.01"))

        points.append(
            ProjectionPoint(
                month=month_start.strftime("%Y-%m"),
                kind="projected",
                balance=running_balance.quantize(Decimal("0.01")),
                low=(running_balance - half_width).quantize(Decimal("0.01")),
                high=(running_balance + half_width).quantize(Decimal("0.01")),
                committed=committed.quantize(Decimal("0.01")),
                components=ProjectionComponents(
                    income_recurring=income_recurring.quantize(Decimal("0.01")),
                    income_variable_estimate=income_variable_estimate,
                    expense_recurring=expense_recurring.quantize(Decimal("0.01")),
                    expense_variable_estimate=expense_variable_estimate,
                    installments_known=installments.quantize(Decimal("0.01")),
                    component_sources={
                        "income_recurring": "regras recorrentes ativas",
                        "income_variable_estimate": "mediana histórica residual",
                        "expense_recurring": "regras recorrentes ativas",
                        "expense_variable_estimate": "mediana histórica residual",
                        "installments_known": "transações conhecidas",
                    },
                    component_windows={
                        "income_recurring": "mês projetado",
                        "income_variable_estimate": "seis meses completos anteriores",
                        "expense_recurring": "mês projetado",
                        "expense_variable_estimate": "seis meses completos anteriores",
                        "installments_known": "mês de vencimento",
                    },
                    income_expected=(income_recurring + (income_variable_estimate or Decimal("0"))).quantize(Decimal("0.01")),
                    recurring=recurring.quantize(Decimal("0.01")),
                    installments=installments.quantize(Decimal("0.01")),
                    variable_estimate=expense_variable_estimate,
                ),
            )
        )

    assumptions = [
        ProjectionAssumption(
            label="Renda esperada",
            value=f"{(income_variable_estimate or Decimal('0')):.2f}",
            source=f"Mediana residual de {len(residual_income)} meses; regras recorrentes separadas",
        ),
        ProjectionAssumption(
            label="Gasto variável estimado",
            value=f"{(expense_variable_estimate or Decimal('0')):.2f}",
            source=f"Mediana residual de {len(residual_consumption)} meses; parcelas separadas",
        ),
        ProjectionAssumption(
            label="Receitas e despesas recorrentes",
            value="componentes separados por tipo",
            source="Regras recorrentes ativas, sem duplicar lançamentos materializados",
        ),
        ProjectionAssumption(
            label="Parcelas conhecidas",
            value="soma mensal por mês futuro",
            source="Transações com total_installments preenchido",
        ),
    ]

    return ProjectionData(points=points, assumptions=assumptions)


async def _total_open_account_balance(
    session: AsyncSession, workspace_id: uuid.UUID, primary_currency: str, as_of: date
) -> Decimal:
    accounts = await _get_open_accounts(session, workspace_id, None)
    total = Decimal("0")
    for account in accounts:
        bal = await _account_balance_at(session, account, as_of)
        converted, _ = await convert(session, Decimal(str(bal)), account.currency, primary_currency)
        total += converted
    return total


# ---------------------------------------------------------------------------
# Goals (R-11)
# ---------------------------------------------------------------------------


async def get_goals(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
) -> list[GoalRow]:
    accounting_mode = await get_credit_card_accounting_mode(session)
    today = date.today()
    trailing = _trailing_month_starts(today, 6, include_current=False)

    result = await session.execute(
        select(Goal).where(
            Goal.workspace_id == workspace_id,
            Goal.status == "active",
        )
    )
    goals = result.scalars().all()

    rows: list[GoalRow] = []
    for goal in goals:
        rows.append(
            await _goal_row(session, goal, primary_currency, accounting_mode, trailing, today)
        )
    return rows


async def _goal_row(
    session: AsyncSession,
    goal: Goal,
    primary_currency: str,
    accounting_mode: str,
    trailing_months: list[date],
    today: date,
) -> GoalRow:
    current = goal.current_amount_primary if goal.current_amount_primary is not None else goal.current_amount
    target = goal.target_amount_primary if goal.target_amount_primary is not None else goal.target_amount
    if goal.tracking_type == "account" and goal.account_id is not None:
        account = await session.get(Account, goal.account_id)
        if account is not None:
            account_balance = await _account_balance_at(session, account, today)
            current, _ = await convert(
                session, Decimal(str(account_balance)), account.currency, primary_currency
            )
    if goal.currency != primary_currency and goal.current_amount_primary is None:
        if not (goal.tracking_type == "account" and goal.account_id is not None):
            current, _ = await convert(session, goal.current_amount, goal.currency, primary_currency)
    if goal.currency != primary_currency and goal.target_amount_primary is None:
        target, _ = await convert(session, goal.target_amount, goal.currency, primary_currency)

    progress = float(current / target) if target else 0.0

    monthly_contributions: list[Decimal]
    blocked_reason = None
    if goal.tracking_type == "account":
        if goal.account_id is None:
            monthly_contributions = []
            blocked_reason = "Meta sem conta vinculada."
        else:
            monthly_contributions = await _goal_account_contributions(
                session, goal.account_id, primary_currency, accounting_mode, trailing_months
            )
    elif goal.tracking_type == "asset":
        if goal.asset_id is None:
            monthly_contributions = []
            blocked_reason = "Meta sem ativo vinculado."
        else:
            monthly_contributions = await _goal_asset_contributions(
                session, goal.asset_id, primary_currency, trailing_months
            )
    elif goal.tracking_type == "manual":
        # Manual tracking has no per-month ledger to sample — contribution
        # is the total delta from initial_amount to current_amount spread
        # as a single-sample "median" (no way to bucket by month without a
        # history table this schema doesn't have).
        delta = current - (goal.initial_amount or Decimal("0"))
        monthly_contributions = [delta] if delta != 0 else [Decimal("0")]
    else:
        monthly_contributions = []
        blocked_reason = f"Tipo de rastreamento não suportado para estimativa: {goal.tracking_type}."

    observed_contribution: Optional[Decimal] = None
    estimated_completion: Optional[date] = None
    status: Literal["on_track", "behind", "stalled", "unknown"]

    if blocked_reason is not None:
        status = "unknown"
    elif not monthly_contributions:
        status = "unknown"
        blocked_reason = "Sem dados suficientes para estimar contribuição."
    else:
        observed_contribution = Decimal(
            str(statistics.median([float(v) for v in monthly_contributions]))
        ).quantize(Decimal("0.01"))

        if observed_contribution <= 0:
            # Projecting a completion date from zero/negative contribution
            # yields infinity; "parada" (stalled) is the true answer.
            status = "stalled"
            estimated_completion = None
        else:
            remaining = target - current
            if remaining <= 0:
                status = "on_track"
                estimated_completion = today
            else:
                months_needed = math.ceil(float(remaining / observed_contribution))
                estimated_completion = _shift_month(_month_start(today), months_needed)
                if goal.target_date is not None and estimated_completion > goal.target_date:
                    status = "behind"
                else:
                    status = "on_track"

    required_contribution: Optional[Decimal] = None
    if goal.target_date is not None:
        months_remaining = _months_between(today, goal.target_date)
        remaining_amount = target - current
        if months_remaining > 0 and remaining_amount > 0:
            required_contribution = (remaining_amount / months_remaining).quantize(Decimal("0.01"))
        elif remaining_amount > 0:
            # Target date already passed (or is this month) with the goal
            # unmet — the expired-with-no-progress case (issue: a real goal
            # has current_amount=0.00 and a target_date days away).
            required_contribution = remaining_amount.quantize(Decimal("0.01"))
            if blocked_reason is None:
                blocked_reason = "Data-alvo já expirada ou expira neste mês, com a meta ainda não atingida."
            if status == "unknown":
                status = "behind"

    return GoalRow(
        goal_id=str(goal.id),
        label=goal.name,
        current=current.quantize(Decimal("0.01")),
        target=target.quantize(Decimal("0.01")),
        progress=progress,
        target_date=goal.target_date,
        observed_contribution=observed_contribution,
        estimated_completion=estimated_completion,
        required_contribution=required_contribution,
        status=status,
        blocked_reason=blocked_reason,
    )


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


async def _goal_account_contributions(
    session: AsyncSession,
    account_id: uuid.UUID,
    primary_currency: str,
    accounting_mode: str,
    month_starts: list[date],
) -> list[Decimal]:
    """Monthly credits to the goal's account, excluding credits that are
    one leg of a paired transfer (transfer_pair_id set) — otherwise moving
    money between the user's own accounts looks like progress toward the
    goal."""
    if not month_starts:
        return []
    date_col = reporting_date_col(accounting_mode)
    window_start = month_starts[0]
    window_end = _month_range(month_starts[-1])[1]

    result = await session.execute(
        select(
            date_col.label("reported_on"),
            Transaction.currency,
            Transaction.amount,
            Transaction.amount_primary,
        )
        .where(
            Transaction.account_id == account_id,
            Transaction.type == "credit",
            Transaction.transfer_pair_id.is_(None),
            date_col >= window_start,
            date_col < window_end,
        )
    )
    per_month: dict[date, Decimal] = {m: Decimal("0") for m in month_starts}
    for reported_on, currency, amount, amount_primary in result.all():
        month_start = reported_on.replace(day=1)
        if month_start not in per_month:
            continue
        if currency == primary_currency:
            converted = Decimal(str(amount or 0))
        elif amount_primary is not None:
            converted = Decimal(str(amount_primary))
        else:
            converted, _ = await convert(session, Decimal(str(amount or 0)), currency, primary_currency)
        per_month[month_start] += converted
    return list(per_month.values())


async def _goal_asset_contributions(
    session: AsyncSession,
    asset_id: uuid.UUID,
    primary_currency: str,
    month_starts: list[date],
) -> list[Decimal]:
    """contribution = delta(value) - yield, i.e. the portion of the asset's
    value change attributable to money the user actually put in (buys minus
    sells at cost) rather than market movement."""
    if not month_starts:
        return []
    asset_result = await session.scalar(select(Asset).where(Asset.id == asset_id))
    if asset_result is None:
        return []
    asset = asset_result
    currency = asset.currency

    contributions: list[Decimal] = []
    for month_start in month_starts:
        month_end = _month_range(month_start)[1]

        trade_result = await session.execute(
            select(AssetTransaction.kind, AssetTransaction.quantity, AssetTransaction.price, AssetTransaction.fee)
            .where(
                AssetTransaction.asset_id == asset_id,
                AssetTransaction.date >= month_start,
                AssetTransaction.date < month_end,
            )
        )
        # contribution = delta(value) - yield = net cash the user put in via
        # trades this month (buys minus sells at cost). This is exactly what
        # the trade ledger records directly — delta(value) only matters for
        # deriving "yield" itself, which isn't surfaced by this endpoint.
        net_trade_cashflow = Decimal("0")
        for kind, quantity, price, fee in trade_result.all():
            cost = quantity * price + (fee or Decimal("0"))
            if kind == "buy":
                net_trade_cashflow += cost
            else:
                net_trade_cashflow -= cost

        converted_trade, _ = await convert(session, net_trade_cashflow, currency, primary_currency)
        contributions.append(converted_trade)

    return contributions


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


async def get_alerts(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
) -> list[AlertRow]:
    """Derived from what the other endpoints already compute: overspend vs
    reference, hygiene gaps, cash gaps, goals off track. Anomaly detection
    (overspend) uses the modified z-score (0.6745 constant), threshold 3.5,
    requiring MAD > 0."""
    accounting_mode = await get_credit_card_accounting_mode(session)
    today = date.today()

    alerts: list[AlertRow] = []
    alerts.extend(await _overspend_alerts(session, workspace_id, primary_currency, accounting_mode, today))
    alerts.extend(await _hygiene_alerts(session, workspace_id, primary_currency))
    alerts.extend(await _cash_gap_alerts(session, workspace_id, primary_currency))
    alerts.extend(await _goal_off_track_alerts(session, workspace_id, primary_currency))

    severity_order = {"crit": 0, "warn": 1, "info": 2}
    alerts.sort(key=lambda a: severity_order.get(a.severity, 3))
    return alerts


async def _overspend_alerts(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    primary_currency: str,
    accounting_mode: str,
    today: date,
) -> list[AlertRow]:
    """Per category, compare the current month's spend against its trailing
    monthly history using the modified z-score. |z| > 3.5 with MAD > 0 flags
    an anomaly; MAD == 0 (a category that never varies) can't produce a
    meaningful z-score and is skipped rather than raising a false alert."""
    trailing = _trailing_month_starts(today, 6, include_current=False)
    current_month = _month_start(today)
    date_col = reporting_date_col(accounting_mode)

    all_months = trailing + [current_month]
    window_start = all_months[0]
    window_end = _month_range(all_months[-1])[1]

    result = await session.execute(
        select(
            Transaction.category_id,
            date_col.label("reported_on"),
            func.sum(_transaction_primary_amount(primary_currency)).label("total"),
        )
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.type == "debit",
            date_col >= window_start,
            date_col < window_end,
            counts_as_user_pnl(),
        )
        .group_by(Transaction.category_id, date_col)
    )

    per_category_month: dict[tuple, Decimal] = {}
    for category_id, reported_on, total in result.all():
        month_start = reported_on.replace(day=1)
        if month_start not in all_months:
            continue
        key = (category_id, month_start)
        per_category_month[key] = per_category_month.get(key, Decimal("0")) + Decimal(str(total or 0))

    categories = await _get_categories_meta(session, workspace_id)

    alerts: list[AlertRow] = []
    category_ids = {cid for cid, _m in per_category_month}
    for category_id in category_ids:
        history = [
            float(per_category_month.get((category_id, m), Decimal("0"))) for m in trailing
        ]
        current_value = float(per_category_month.get((category_id, current_month), Decimal("0")))
        if len(history) < MIN_MONTHS_FOR_RUNWAY:
            continue

        median = statistics.median(history)
        mad = statistics.median([abs(v - median) for v in history])
        if mad <= 0:
            continue

        z = _MODIFIED_ZSCORE_CONSTANT * (current_value - median) / mad
        if abs(z) <= _MODIFIED_ZSCORE_THRESHOLD or current_value <= median:
            continue

        meta = categories.get(category_id)
        label = meta["label"] if meta else "Sem categoria"
        alerts.append(
            AlertRow(
                id=f"overspend:{category_id}:{current_month.isoformat()}",
                severity="warn" if abs(z) < 5 else "crit",
                kind="overspend",
                title=f"Gasto atípico em {label}",
                detail=(
                    f"{label} está em {current_value:.2f} este mês, bem acima da "
                    f"mediana histórica de {median:.2f}."
                ),
                amount=Decimal(str(round(current_value - median, 2))),
                link=AlertLink(view="categories", params={"category_id": str(category_id)}),
            )
        )
    return alerts


async def _hygiene_alerts(
    session: AsyncSession, workspace_id: uuid.UUID, primary_currency: str
) -> list[AlertRow]:
    hygiene = await get_hygiene(session, workspace_id, primary_currency)
    alerts: list[AlertRow] = []
    if hygiene.coverage.categorized.total > 0 and hygiene.coverage.categorized.pct < 80:
        alerts.append(
            AlertRow(
                id="hygiene:categorized",
                severity="info",
                kind="hygiene",
                title="Transações sem categoria",
                detail=(
                    f"Só {hygiene.coverage.categorized.pct:.0f}% das transações estão "
                    f"categorizadas ({hygiene.coverage.categorized.done} de "
                    f"{hygiene.coverage.categorized.total})."
                ),
                amount=None,
                link=AlertLink(view="hygiene", params={}),
            )
        )
    return alerts


async def _cash_gap_alerts(
    session: AsyncSession, workspace_id: uuid.UUID, primary_currency: str
) -> list[AlertRow]:
    """A projected month whose balance's pessimistic (low) bound crosses
    zero is a real cash-flow risk worth surfacing, not just a chart detail."""
    projection = await get_projection(session, workspace_id, primary_currency)
    alerts: list[AlertRow] = []
    for point in projection.points:
        if point.kind != "projected" or point.low is None:
            continue
        if point.low < 0:
            alerts.append(
                AlertRow(
                    id=f"cash_gap:{point.month}",
                    severity="crit",
                    kind="cash_gap",
                    title=f"Possível saldo negativo em {point.month}",
                    detail=(
                        f"No cenário pessimista, o saldo projetado para {point.month} "
                        f"fica negativo (mínimo estimado de {point.low:.2f})."
                    ),
                    amount=point.low,
                    link=AlertLink(view="projection", params={"month": point.month}),
                )
            )
            break  # Only the first crossing is actionable; later months inherit the same warning.
    return alerts


async def _goal_off_track_alerts(
    session: AsyncSession, workspace_id: uuid.UUID, primary_currency: str
) -> list[AlertRow]:
    goals = await get_goals(session, workspace_id, primary_currency)
    alerts: list[AlertRow] = []
    for goal in goals:
        if goal.status in ("behind", "stalled"):
            severity: Literal["crit", "warn", "info"] = "crit" if goal.status == "stalled" else "warn"
            detail = (
                "Sem contribuição observada nos últimos meses."
                if goal.status == "stalled"
                else f"Ritmo atual projeta conclusão além da data-alvo ({goal.target_date})."
            )
            alerts.append(
                AlertRow(
                    id=f"goal_off_track:{goal.goal_id}",
                    severity=severity,
                    kind="goal_off_track",
                    title=f"Meta \"{goal.label}\" fora do ritmo",
                    detail=detail,
                    amount=None,
                    link=AlertLink(view="goals", params={"goal_id": goal.goal_id}),
                )
            )
    return alerts
