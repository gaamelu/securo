"""Service layer for the Insights tab (GET /api/insights/*).

Income-independent endpoints only (hygiene, categories). See
`backend/app/schemas/insights.py` for the response contracts and
`backend/app/services/_query_filters.py` for the shared P&L definition
these queries reuse rather than duplicate.
"""
import statistics
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction
from app.schemas.insights import (
    BreakevenRow,
    BreakevenTableData,
    CategoriesData,
    CategoryRow,
    FlexibilityImpact,
    HygieneCoverage,
    HygieneCoverageStat,
    HygieneData,
    HygieneOffender,
    InsightsEnvelope,
    InsightsError,
    NatureData,
    NatureMonth,
    NatureShares,
    NatureValues,
    PurchaseDecisionData,
    PurchaseDecisionRequest,
    PurchaseDecisionScheduleRow,
    PurchaseDecisionVerdict,
    YieldBasis,
    YieldRange,
    YieldWindowDays,
)
from app.services._query_filters import counts_as_user_pnl, reporting_date_col
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.dashboard_service import _month_range
from app.services.fx_rate_service import convert

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

# How many worst offenders to surface in the hygiene block.
_WORST_OFFENDERS_LIMIT = 5


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
        select(func.count(Transaction.id)).where(Transaction.workspace_id == workspace_id)
    )
    total_txs = total_result.scalar_one()

    categorized_result = await session.execute(
        select(func.count(Transaction.id)).where(
            Transaction.workspace_id == workspace_id,
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
        ).where(
            Transaction.workspace_id == workspace_id,
            Transaction.category_id.is_(None),
        )
    )
    rows = result.all()

    # Multi-currency: convert each (month, account, payee, currency) bucket
    # to primary currency before ranking, then merge same (month, account,
    # payee) buckets that differ only by currency.
    merged: dict[tuple, dict] = {}
    for reported_on, account_id, payee, currency, amount in rows:
        month = reported_on.replace(day=1) if reported_on else None
        key = (month, account_id, payee)
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
            Transaction.currency,
            func.sum(Transaction.amount).label("total"),
            func.count(Transaction.id).label("tx_count"),
        )
        .where(
            Transaction.workspace_id == workspace_id,
            date_col >= target_month,
            date_col < next_month,
            Transaction.type == "debit",
            counts_as_user_pnl(),
        )
        .group_by(Transaction.category_id, Transaction.currency)
    )
    actual_rows = actual_result.all()

    actual_by_category: dict[Optional[uuid.UUID], dict] = {}
    for category_id, currency, total, tx_count in actual_rows:
        converted, _ = await convert(session, Decimal(str(total or 0)), currency, primary_currency)
        bucket = actual_by_category.setdefault(category_id, {"amount": Decimal("0"), "tx_count": 0})
        bucket["amount"] += converted
        bucket["tx_count"] += tx_count

    categories = await _get_categories_meta(session, workspace_id)

    if reference == "budget":
        reference_by_category, reference_method = await _budget_reference(
            session, workspace_id, primary_currency, target_month
        )
        window = None
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
                window=None,
                reference=reference,
                currency=primary_currency,
                generated_at=datetime.now(timezone.utc),
            )
        window = None

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
    result = await session.execute(
        select(Budget.category_id, Budget.currency, Budget.amount).where(
            Budget.workspace_id == workspace_id,
            Budget.month == target_month,
        )
    )
    out: dict[Optional[uuid.UUID], Decimal] = {}
    for category_id, currency, amount in result.all():
        ccy = currency or primary_currency
        converted, _ = await convert(session, Decimal(str(amount)), ccy, primary_currency)
        out[category_id] = out.get(category_id, Decimal("0")) + converted
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
                Transaction.currency,
                func.sum(Transaction.amount).label("total"),
            )
            .where(
                Transaction.workspace_id == workspace_id,
                Transaction.type == "debit",
                date_col >= month_start,
                date_col < month_end,
                counts_as_user_pnl(),
            )
            .group_by(Transaction.category_id, Transaction.currency)
        )
        months_seen.add(month_start)
        for category_id, currency, total in result.all():
            converted, _ = await convert(
                session, Decimal(str(total or 0)), currency, primary_currency
            )
            key = (category_id, month_start)
            per_category_month[key] = per_category_month.get(key, Decimal("0")) + converted

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

    effective_nature = func.coalesce(Transaction.expense_nature, Category.expense_nature)

    series: list[NatureMonth] = []
    for month_start in month_starts:
        month_end = _month_range(month_start)[1]
        result = await session.execute(
            select(
                effective_nature.label("nature"),
                Transaction.currency,
                func.sum(Transaction.amount).label("total"),
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
            .group_by(effective_nature, Transaction.currency)
        )
        rows = result.all()

        totals: dict[str, Decimal] = {key: Decimal("0") for key in _NATURE_KEYS}
        for nature, currency, total in rows:
            key = nature if nature in ("fixed", "variable", "discretionary") else "unclassified"
            converted, _ = await convert(session, Decimal(str(total or 0)), currency, primary_currency)
            totals[key] += converted

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
            shares = NatureShares(
                fixed=float(totals["fixed"] / month_total),
                variable=float(totals["variable"] / month_total),
                discretionary=float(totals["discretionary"] / month_total),
                unclassified=float(totals["unclassified"] / month_total),
            )

        series.append(
            NatureMonth(
                month=month_start.strftime("%Y-%m"),
                trusted=True,
                income=None,
                values=values,
                shares=shares,
            )
        )

    return NatureData(series=series, reference=None)


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
        method="observed",
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
