// frontend/src/types/insights.ts
//
// Mirrors backend/app/schemas/insights.py. Ground truth for field names is
// that file's docstring, which itself defers to mock-src/fixtures.py where
// the two disagree — see the NOTE comments there.
//
// House rules carried over from the backend:
// - Money is ALWAYS a fixed-2-decimal STRING at the JSON boundary
//   (e.g. "1234.56"), never a number. Parse with `parseMoney` from
//   `@/lib/insights-utils` only where a computation actually needs a number.
// - Ratios/percentages (delta_pct, progress, shares, breakeven_discount) are
//   plain floats — not money, no string encoding.
// - The tab never computes date arithmetic client-side: days_since_review,
//   coverage pct, etc. all arrive pre-computed from the server.

/** Fixed-2-decimal string, e.g. "1234.56" or "-42.00". Never a number. */
export type Money = string

// ---------------------------------------------------------------------------
// Error envelope
// ---------------------------------------------------------------------------

export type InsightsErrorCode =
  | 'INSUFFICIENT_HISTORY'
  | 'NOT_CLASSIFIED'
  | 'NO_TRUSTED_WINDOW'
  | 'NO_YIELD_BASIS'
  | 'COMPUTATION_FAILED'

export interface InsightsError {
  code: InsightsErrorCode
  message: string
  retryable: boolean
}

export interface InsightsWindow {
  // Serialized as `from` on the wire (populate_by_name alias in the backend
  // model) — `from` is a reserved word in some JS/TS contexts (e.g. as an
  // identifier in certain grammars, and it shadows Array.from-style usage
  // awkwardly), so the field is kept as `from` here since it's valid as an
  // object property key/string literal in TypeScript; access it via
  // `window.from` or destructure with `{ from: windowFrom }`.
  from: string
  to: string
  trusted_from: string | null
  months_trusted: number
}

export type InsightsReference = 'budget' | 'historical'

export interface InsightsEnvelope<T> {
  data: T | null
  error: InsightsError | null
  window: InsightsWindow | null
  reference: InsightsReference | null
  currency: string
  generated_at: string
}

// ---------------------------------------------------------------------------
// 1 - Alerts
// ---------------------------------------------------------------------------

export interface AlertLink {
  view: string
  params: Record<string, string>
}

export interface AlertRow {
  id: string
  severity: 'crit' | 'warn' | 'info'
  kind: 'overspend' | 'hygiene' | 'cash_gap' | 'goal_off_track'
  title: string
  detail: string
  amount: Money | null
  link: AlertLink | null
}

export type AlertsData = AlertRow[]

// ---------------------------------------------------------------------------
// 2 - Vitals
// ---------------------------------------------------------------------------

export interface VitalReference {
  value: Money
  source: 'budget' | 'historical' | 'benchmark'
  method: string | null
  label: string
}

export interface VitalSeriesPoint {
  date: string
  value: Money
  trusted: boolean
}

export interface VitalCard {
  key: 'runway' | 'savings_rate' | 'net_worth' | 'credit_utilization'
  label: string
  value: Money | null
  unit: 'BRL' | 'months' | 'percent'
  reference: VitalReference | null
  status: 'good' | 'warn' | 'crit' | 'unknown'
  available: boolean
  blocked_reason: string | null
  series: VitalSeriesPoint[] | null
}

export type VitalsData = VitalCard[]

// ---------------------------------------------------------------------------
// 3 - Flow (Sankey)
// ---------------------------------------------------------------------------

export interface FlowNode {
  id: string
  label: string
  depth: 0 | 1 | 2
  kind: 'income' | 'group' | 'category' | 'saved'
  color: string
  value: Money
}

export interface FlowLink {
  source: string
  target: string
  value: Money
}

export interface FlowData {
  nodes: FlowNode[]
  links: FlowLink[]
  collapse_threshold: Money
  // Optional: the empty-month fixture omits these entirely even though the
  // design brief says they should "always" be present. Backend schema makes
  // them optional to tolerate that; do the same here.
  income_total: Money | null
  deficit: Money | null
}

// ---------------------------------------------------------------------------
// 4 - Nature (fixed / variable / discretionary)
// ---------------------------------------------------------------------------

export interface NatureShares {
  fixed: number
  variable: number
  discretionary: number
  unclassified: number
}

export interface NatureValues {
  fixed: Money
  variable: Money
  discretionary: Money
  unclassified: Money
}

export interface NatureMonth {
  month: string
  trusted: boolean
  income: Money | null
  values: NatureValues
  shares: NatureShares | null
}

export interface NatureReference {
  fixed: Money
  variable: Money
  discretionary: Money
  unclassified: Money
}

export interface NatureData {
  series: NatureMonth[]
  // NOTE: fixtures.py never populates this even though contratos.html's
  // "Forma do dado" lists it at the top level. Defensive: treat as possibly
  // absent/null.
  reference: NatureReference | null
}

// ---------------------------------------------------------------------------
// 5 - Projection
// ---------------------------------------------------------------------------

export interface ProjectionComponents {
  income_expected: Money
  recurring: Money
  installments: Money
  variable_estimate: Money
}

export interface ProjectionPoint {
  month: string
  kind: 'actual' | 'projected'
  balance: Money
  low: Money | null
  high: Money | null
  committed: Money
  components: ProjectionComponents
}

export interface ProjectionAssumption {
  label: string
  value: string
  source: string
}

export interface ProjectionData {
  points: ProjectionPoint[]
  assumptions: ProjectionAssumption[]
}

// ---------------------------------------------------------------------------
// 6 - Categories table
// ---------------------------------------------------------------------------

export interface CategoryRow {
  category_id: string | null
  label: string
  color: string
  group_label: string
  nature: 'fixed' | 'variable' | 'discretionary' | null
  amount: Money
  reference: Money | null
  reference_method: string | null
  delta: Money | null
  delta_pct: number | null
  tx_count: number
  status: 'over' | 'under' | 'ok' | 'no_ref'
}

export interface CategoriesData {
  rows: CategoryRow[]
  available: boolean
  blocked_reason: string | null
}

// ---------------------------------------------------------------------------
// 7 - Hygiene
// ---------------------------------------------------------------------------

export interface HygieneCoverageStat {
  done: number
  total: number
  // Server-computed (a `computed_field` on the backend model) — never derive
  // this client-side, per the "screen never computes" house rule.
  pct: number
}

export interface HygieneCoverage {
  categorized: HygieneCoverageStat
  nature_set: HygieneCoverageStat
  transfers_paired: HygieneCoverageStat
}

export interface HygieneOffender {
  kind: 'month' | 'account' | 'payee'
  label: string
  missing: number
  amount: Money
}

export interface HygieneData {
  coverage: HygieneCoverage
  last_review_at: string | null
  // NOTE: fixtures.py never populates this field even though the design
  // brief and contratos.html both call for it. Treat as possibly null/absent
  // and never derive it client-side from last_review_at (that would violate
  // the "client must never divide or subtract dates" rule the field exists
  // to satisfy in the first place).
  days_since_review: number | null
  worst_offenders: HygieneOffender[]
}

// ---------------------------------------------------------------------------
// 8 - Goals
// ---------------------------------------------------------------------------

export interface GoalRow {
  goal_id: string
  label: string
  current: Money
  target: Money
  progress: number
  target_date: string | null
  observed_contribution: Money | null
  estimated_completion: string | null
  required_contribution: Money | null
  status: 'on_track' | 'behind' | 'stalled' | 'unknown'
  blocked_reason: string | null
}

export type GoalsData = GoalRow[]

// ---------------------------------------------------------------------------
// 9 - Breakeven table + purchase decision calculator
// ---------------------------------------------------------------------------

export interface YieldRange {
  p25: string
  p75: string
}

export interface YieldWindowDays {
  min: number
  max: number
}

export interface YieldBasis {
  monthly_gross: string
  method: 'observed'
  sample_size: number
  window_days: YieldWindowDays
  range: YieldRange
  as_of: string
}

export interface BreakevenRow {
  n: number
  ir_rate: number
  net_monthly: string
  breakeven_discount: number
  gain_per_1000: Money
}

export interface BreakevenTableData {
  yield_basis: YieldBasis
  rows: BreakevenRow[]
}

export interface PurchaseDecisionRequest {
  price: Money
  cash_discount_pct: number
  installments: number
  yield_override: number | null
}

export interface PurchaseDecisionVerdict {
  choice: 'installments' | 'cash'
  net_gain: Money
  breakeven_discount: number
  headline: string
}

export interface PurchaseDecisionScheduleRow {
  month: number
  payment: Money
  balance_invested: Money
  yield_net: Money
  cumulative_gain: Money
}

export interface FlexibilityMonthlyCommitmentDelta {
  month: string
  // NOTE: unconfirmed shape — contratos.html only sketches `{month, ...}`
  // and no fixture exercises this endpoint (it's a live POST calculator).
  delta: Money
}

export interface FlexibilityImpact {
  utilization_before: number
  utilization_after: number
  monthly_commitment_delta: FlexibilityMonthlyCommitmentDelta[]
  cash_gap_month: string | null
}

export interface PurchaseDecisionData {
  verdict: PurchaseDecisionVerdict
  schedule: PurchaseDecisionScheduleRow[]
  flexibility_impact: FlexibilityImpact
}
