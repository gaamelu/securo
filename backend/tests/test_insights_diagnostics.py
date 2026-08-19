from datetime import date
from decimal import Decimal

from app.services.insights_diagnostics import (
    nature_shares,
    projection_months,
    savings_rate,
    security_status,
)


def test_savings_rate_uses_net_savings_contribution_not_leftover_cash():
    assert savings_rate(Decimal("300"), Decimal("1000")) == Decimal("30.00")
    assert savings_rate(Decimal("0"), Decimal("1000")) == Decimal("0.00")


def test_nature_shares_partition_total_spend():
    shares = nature_shares({
        "fixed": Decimal("100"),
        "variable": Decimal("50"),
        "discretionary": Decimal("25"),
        "unclassified": Decimal("25"),
    })
    assert sum(shares.values()) == Decimal("1")


def test_projection_has_twelve_actual_then_twelve_projected_months():
    points = projection_months(date(2026, 8, 19))
    assert len(points) == 24
    assert [kind for _, kind in points[:12]] == ["actual"] * 12
    assert [kind for _, kind in points[12:]] == ["projected"] * 12
    assert len({month for month, _ in points}) == 24


def test_security_status_uses_coverage_and_projected_floor():
    assert security_status(Decimal("6"), Decimal("100"), Decimal("100")) == "safe"
    assert security_status(Decimal("4"), Decimal("100"), Decimal("100")) == "attention"
    assert security_status(Decimal("2"), Decimal("100"), Decimal("100")) == "risk"
    assert security_status(Decimal("7"), Decimal("99"), Decimal("100")) == "attention"
