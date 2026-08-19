"""Schemas for the Insights tab (GET /api/insights/*).

Ground truth for field names and nesting is the executable fixtures mock:
mock-src/fixtures.py (see finance/insights-design/). Where this file and the
original design brief disagreed, the fixtures won — discrepancies are noted
inline as comments prefixed with `NOTE:`.

House rules baked into these schemas (see contratos.html):
- Money is always a fixed-2-decimal string in JSON, Decimal internally.
- Ratios/percentages (delta_pct, progress, shares, breakeven_discount) are
  floats — they are not money and don't share its precision problem.
  Frequencies (0.20 for 20%) are represented as floats in [0, 1] unless the
  fixtures show otherwise (they do not always agree, see NOTE below).
- The tab never computes: days_since_review, coverage pct, etc. all arrive
  pre-computed from the server.
- Every block-level response optionally carries `window`/`reference`; the
  calculator (breakeven-table, purchase-decision) and a couple of blocks
  without a reference axis (flow, projection, hygiene) omit them.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, PlainSerializer, computed_field

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Money: Decimal internally, fixed 2-decimal string at the JSON boundary.
# ---------------------------------------------------------------------------

Money = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.2f}", return_type=str)]


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------

# NOTE: contratos.html's decided error contract only lists these codes:
# it doesn't enumerate a closed set of `code` values, it just says "a code
# the screen decides by". The brief asked for a fixed set of five codes;
# fixtures.py never emits an error envelope at all (every scenario returns
# `data` instead), so there is no ground truth for the exact code strings.
# Following the brief's five codes as the closed set since it's the only
# guidance available; COMPUTATION_FAILED is the only retryable one.
InsightsErrorCode = Literal[
    "INSUFFICIENT_HISTORY",
    "NOT_CLASSIFIED",
    "NO_TRUSTED_WINDOW",
    "NO_YIELD_BASIS",
    "COMPUTATION_FAILED",
]


class InsightsError(BaseModel):
    code: InsightsErrorCode
    message: str
    retryable: bool = False


class InsightsWindow(BaseModel):
    model_config = {"populate_by_name": True}

    from_: date = Field(alias="from")
    to: date
    trusted_from: date | None
    months_trusted: int


class InsightsEnvelope(BaseModel, Generic[T]):
    data: T | None
    error: InsightsError | None = None
    window: InsightsWindow | None = None
    reference: Literal["budget", "historical"] | None = None
    currency: str
    generated_at: datetime


# ---------------------------------------------------------------------------
# 1 - Alerts
# ---------------------------------------------------------------------------


class AlertLink(BaseModel):
    view: str
    params: dict[str, str]


class AlertRow(BaseModel):
    id: str
    severity: Literal["crit", "warn", "info"]
    kind: Literal["overspend", "hygiene", "cash_gap", "goal_off_track"]
    title: str
    detail: str
    amount: Money | None
    link: AlertLink | None


# `alerts` in fixtures.py is a bare list, not wrapped in an extra object.
AlertsData = list[AlertRow]


# ---------------------------------------------------------------------------
# 2 - Vitals
# ---------------------------------------------------------------------------


class VitalReference(BaseModel):
    value: Money
    source: Literal["budget", "historical", "benchmark"]
    method: str | None
    label: str


class VitalSeriesPoint(BaseModel):
    date: str
    value: Money
    trusted: bool


class VitalCard(BaseModel):
    key: Literal["runway", "savings_rate", "net_worth", "credit_utilization"]
    label: str
    value: Money | None
    unit: Literal["BRL", "months", "percent"]
    reference: VitalReference | None
    status: Literal["good", "warn", "crit", "unknown"]
    available: bool
    blocked_reason: str | None
    series: list[VitalSeriesPoint] | None


# `vitals` in fixtures.py is a bare list of the four cards.
VitalsData = list[VitalCard]


# ---------------------------------------------------------------------------
# 3 - Flow (Sankey)
# ---------------------------------------------------------------------------


class FlowNode(BaseModel):
    id: str
    label: str
    depth: Literal[0, 1, 2]
    kind: Literal["income", "group", "category", "saved"]
    color: str
    value: Money


class FlowLink(BaseModel):
    source: str
    target: str
    value: Money


class FlowData(BaseModel):
    nodes: list[FlowNode]
    links: list[FlowLink]
    collapse_threshold: Money
    # NOTE: fixtures.py only sets income_total/deficit in the non-empty
    # branch; the "vazio" (empty) scenario returns
    # dict(nodes=[], links=[], collapse_threshold="0.00") with no
    # income_total/deficit keys at all. contratos.html says deficit must
    # "sempre vir" (always be present), so we make them optional here
    # rather than required, to tolerate the empty-month case the fixtures
    # actually produce.
    income_total: Money | None = None
    deficit: Money | None = None


# ---------------------------------------------------------------------------
# 4 - Nature (fixed / variable / discretionary)
# ---------------------------------------------------------------------------


class NatureShares(BaseModel):
    fixed: float
    variable: float
    discretionary: float
    unclassified: float


class NatureValues(BaseModel):
    fixed: Money
    variable: Money
    discretionary: Money
    unclassified: Money


class NatureMonth(BaseModel):
    month: str
    trusted: bool
    income: Money | None
    values: NatureValues
    shares: NatureShares | None


class NatureReference(BaseModel):
    fixed: Money
    variable: Money
    discretionary: Money
    unclassified: Money


class NatureData(BaseModel):
    series: list[NatureMonth]
    # NOTE: fixtures.py's `nature()` never populates a `reference` key —
    # the mock's build() only calls nature() with no reference argument
    # and the dict it returns has no such field. contratos.html's "Forma
    # do dado" for block 4 does list `reference: {...}|None` at the top
    # level though, so we keep it here as the documented contract but
    # default it to None since the fixtures never exercise it.
    reference: NatureReference | None = None


# ---------------------------------------------------------------------------
# 5 - Projection
# ---------------------------------------------------------------------------


class ProjectionComponents(BaseModel):
    income_expected: Money
    recurring: Money
    installments: Money
    variable_estimate: Money


class ProjectionPoint(BaseModel):
    month: str
    kind: Literal["actual", "projected"]
    balance: Money
    low: Money | None
    high: Money | None
    committed: Money
    components: ProjectionComponents


class ProjectionAssumption(BaseModel):
    label: str
    value: str
    source: str


class ProjectionData(BaseModel):
    points: list[ProjectionPoint]
    assumptions: list[ProjectionAssumption]


# ---------------------------------------------------------------------------
# 6 - Categories table
# ---------------------------------------------------------------------------


class CategoryRow(BaseModel):
    category_id: str | None
    label: str
    color: str
    group_label: str
    nature: Literal["fixed", "variable", "discretionary"] | None
    amount: Money
    reference: Money | None
    reference_method: str | None
    delta: Money | None
    delta_pct: float | None
    tx_count: int
    status: Literal["over", "under", "ok", "no_ref"]


class CategoriesData(BaseModel):
    rows: list[CategoryRow]
    available: bool
    blocked_reason: str | None


class CategoriesByReference(BaseModel):
    """fixtures.py nests both reference modes under one response:
    dict(categories=dict(budget=categories("budget"), historical=categories("historical")))
    NOTE: this is a mock-build-time convenience (both modes computed once
    for the whole scenario dump), not necessarily the real endpoint shape —
    contratos.html's per-endpoint contract for `/api/insights/categories`
    takes a `reference` query param and returns a single CategoriesData,
    matching the brief. We model both: CategoriesData for the real
    per-request endpoint, and this wrapper only if a combined shape is
    ever needed. The real endpoint should return InsightsEnvelope[CategoriesData].
    """

    budget: CategoriesData
    historical: CategoriesData


# ---------------------------------------------------------------------------
# 7 - Hygiene
# ---------------------------------------------------------------------------


class HygieneCoverageStat(BaseModel):
    done: int
    total: int

    # The screen never computes. `fixtures.py` emits only {done, total} and
    # leaves the division to the client, which is the defect the review filed
    # as G8 — the mock was never corrected, so the fixtures carry it forward.
    # Deriving `pct` here rather than storing it keeps it from drifting away
    # from the counts it summarizes.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def pct(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.done / self.total * 100, 1)


class HygieneCoverage(BaseModel):
    categorized: HygieneCoverageStat
    nature_set: HygieneCoverageStat
    transfers_paired: HygieneCoverageStat


class HygieneOffender(BaseModel):
    kind: Literal["month", "account", "payee"]
    label: str
    missing: int
    amount: Money


class HygieneData(BaseModel):
    coverage: HygieneCoverage
    last_review_at: datetime | None
    # NOTE: fixtures.py's hygiene() never emits `days_since_review` at all —
    # the dict returned is exactly
    # dict(coverage=..., last_review_at=..., worst_offenders=...).
    # The brief explicitly asks for a `days_since_review: int` field
    # ("client must never divide or subtract dates"), and contratos.html's
    # "Forma do dado" for block 7 lists it too
    # (`days_since_review: int|null`). We keep the field since two of the
    # three sources require it and it's clearly intended, but note that
    # the fixtures themselves never populate it — likely an oversight in
    # the mock rather than a deliberate omission.
    days_since_review: int | None = None
    worst_offenders: list[HygieneOffender]


# ---------------------------------------------------------------------------
# 8 - Goals
# ---------------------------------------------------------------------------


class GoalRow(BaseModel):
    goal_id: str
    label: str
    current: Money
    target: Money
    progress: float
    target_date: date | None
    observed_contribution: Money | None
    estimated_completion: date | None
    required_contribution: Money | None
    status: Literal["on_track", "behind", "stalled", "unknown"]
    blocked_reason: str | None


# `goals` in fixtures.py is a bare list.
GoalsData = list[GoalRow]


# ---------------------------------------------------------------------------
# 9 - Breakeven table + purchase decision calculator
# ---------------------------------------------------------------------------


class YieldRange(BaseModel):
    p25: str
    p75: str


class YieldWindowDays(BaseModel):
    min: int
    max: int


class YieldBasis(BaseModel):
    monthly_gross: str
    method: Literal["observed", "assumed"]
    sample_size: int
    window_days: YieldWindowDays
    range: YieldRange
    as_of: date


class BreakevenRow(BaseModel):
    n: int
    ir_rate: float
    net_monthly: str
    breakeven_discount: float
    gain_per_1000: Money


class BreakevenTableData(BaseModel):
    yield_basis: YieldBasis
    rows: list[BreakevenRow]


class PurchaseDecisionRequest(BaseModel):
    price: Money
    cash_discount_pct: float
    installments: int
    yield_override: float | None = None


class PurchaseDecisionVerdict(BaseModel):
    choice: Literal["installments", "cash"]
    net_gain: Money
    breakeven_discount: float
    headline: str


class PurchaseDecisionScheduleRow(BaseModel):
    month: int
    payment: Money
    balance_invested: Money
    yield_net: Money
    cumulative_gain: Money


class FlexibilityMonthlyCommitmentDelta(BaseModel):
    month: str
    # NOTE: contratos.html's schema sketch for
    # monthly_commitment_delta is `[{month, ...}]` — the ellipsis means the
    # remaining fields aren't specified anywhere (not in the brief, not in
    # fixtures.py, which doesn't build purchase-decision responses at all;
    # that endpoint is a POST computed live, outside the fixtures dump).
    # Modeling the one concrete field we have plus a delta amount as the
    # most plausible reading; this needs confirmation once the backend
    # calculator is actually specified.
    delta: Money


class FlexibilityImpact(BaseModel):
    utilization_before: float
    utilization_after: float
    monthly_commitment_delta: list[FlexibilityMonthlyCommitmentDelta]
    cash_gap_month: str | None


class PurchaseDecisionData(BaseModel):
    verdict: PurchaseDecisionVerdict
    schedule: list[PurchaseDecisionScheduleRow]
    flexibility_impact: FlexibilityImpact
