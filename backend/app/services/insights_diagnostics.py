"""Small, shared invariants for Insights calculations."""

from datetime import date
from decimal import Decimal
from typing import Literal


def savings_rate(contribution: Decimal, external_inflows: Decimal) -> Decimal:
    """Percentage of external income routed to savings accounts."""
    if external_inflows <= 0:
        return Decimal("0.00")
    return (contribution / external_inflows * 100).quantize(Decimal("0.01"))


def nature_shares(values: dict[str, Decimal]) -> dict[str, Decimal]:
    total = sum(values.values(), Decimal("0"))
    if total <= 0:
        return {key: Decimal("0") for key in values}
    return {key: value / total for key, value in values.items()}


def projection_months(as_of: date) -> list[tuple[str, Literal["actual", "projected"]]]:
    """Return 12 historical months including current, then 12 future months."""
    current = date(as_of.year, as_of.month, 1)
    months: list[tuple[str, Literal["actual", "projected"]]] = []
    for offset in range(-11, 13):
        total = current.year * 12 + current.month - 1 + offset
        year, month_zero = divmod(total, 12)
        kind: Literal["actual", "projected"] = "actual" if offset <= 0 else "projected"
        months.append((f"{year:04d}-{month_zero + 1:02d}", kind))
    return months


def security_status(
    coverage_months: Decimal,
    minimum_projected_balance: Decimal,
    essential_monthly_cost: Decimal,
) -> Literal["safe", "attention", "risk"]:
    if coverage_months < 3 or minimum_projected_balance < 0:
        return "risk"
    if coverage_months < 6 or minimum_projected_balance < essential_monthly_cost:
        return "attention"
    return "safe"
