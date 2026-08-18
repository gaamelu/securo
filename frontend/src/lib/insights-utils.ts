// frontend/src/lib/insights-utils.ts
//
// Pure helpers for the Insights tab. Kept dependency-free and DOM-free on
// purpose: vitest.config.ts runs with `environment: 'node'` (no jsdom, no
// testing-library anywhere in this project), so any logic that needs a unit
// test has to live here as a plain function rather than inside a component.

/** A delta's direction relative to its reference value. */
export type DeltaStatus = 'over' | 'under' | 'neutral'

export interface FormattedDelta {
  /** '+' | '-' | '' — '' only for an exact-zero delta. */
  sign: '+' | '-' | ''
  /** Absolute difference between actual and reference, always >= 0. */
  magnitude: number
  /** 'over' when actual > reference, 'under' when actual < reference, 'neutral' when equal. */
  status: DeltaStatus
}

/**
 * Parses a backend money string (fixed 2-decimal, e.g. "1234.56" or
 * "-42.00") into a number. Money always arrives as a string on this tab —
 * never trust a numeric literal from the wire.
 *
 * Returns 0 for null/undefined/empty/unparseable input rather than NaN, so
 * callers can feed the result straight into arithmetic or formatting without
 * an extra guard at every call site. This mirrors the tab's stance that a
 * missing value should render as "no data", not corrupt a sum.
 */
export function parseMoney(value: string | null | undefined): number {
  if (value === null || value === undefined || value.trim() === '') return 0
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

/**
 * Compares an actual money string against a reference money string and
 * returns the signed magnitude plus a status describing the direction.
 *
 * "over"/"under" describe actual relative to reference in raw numeric terms
 * (actual > reference => 'over'); callers that need domain-specific framing
 * (e.g. "over budget is bad, under budget is good" for expenses, but the
 * opposite for income) should map `status` themselves rather than expecting
 * this function to know the category's semantics.
 */
export function formatDelta(actual: string | null | undefined, reference: string | null | undefined): FormattedDelta {
  const actualN = parseMoney(actual)
  const referenceN = parseMoney(reference)
  const diff = actualN - referenceN

  if (diff === 0) {
    return { sign: '', magnitude: 0, status: 'neutral' }
  }

  return {
    sign: diff > 0 ? '+' : '-',
    magnitude: Math.abs(diff),
    status: diff > 0 ? 'over' : 'under',
  }
}

/**
 * Maps a delta/vital status to a design token color. Never hard-code hex in
 * a component for this — go through here so the palette stays centralized.
 * Values match the inline hex the reports tab already uses for the same
 * semantics (see frontend/src/pages/reports.tsx: emerald-600 / rose-500).
 */
export function statusColor(status: DeltaStatus | 'good' | 'warn' | 'crit' | 'unknown' | 'ok' | 'no_ref'): string {
  switch (status) {
    case 'under':
    case 'good':
    case 'ok':
      return 'var(--chart-3)' // emerald
    case 'over':
    case 'crit':
      return 'var(--destructive)' // rose
    case 'warn':
      return 'var(--chart-4)' // amber
    case 'neutral':
    case 'unknown':
    case 'no_ref':
    default:
      return 'var(--muted-foreground)'
  }
}

/**
 * Maps a coverage percentage (already server-computed, 0-100 — see
 * HygieneCoverageStat.pct) to a good/warn/crit status band for display.
 * Thresholds are a display-only convention (>=90 good, >=60 warn, else
 * crit); the server never sends a status for coverage stats, only counts,
 * so this is where "how worried should the badge look" lives, not a
 * recomputation of the percentage itself.
 */
export function coverageStatus(pct: number): 'good' | 'warn' | 'crit' {
  if (pct >= 90) return 'good'
  if (pct >= 60) return 'warn'
  return 'crit'
}

/** One "nature" (fixed/variable/discretionary/unclassified) slice, as a share in [0, 1]. */
export type NatureKey = 'fixed' | 'variable' | 'discretionary' | 'unclassified'

export interface NatureShareInput {
  fixed: number
  variable: number
  discretionary: number
  unclassified: number
}

export interface NatureSharePercent {
  key: NatureKey
  /** 0-100, rounded to 1 decimal. */
  pct: number
}

/**
 * Converts a month's `shares` (fractions in [0, 1], or null when the month
 * had no trusted spend — see NatureMonth.shares) into display-ready
 * percentages, or `null` to signal "sem dados". A month with null shares
 * must never render as four 0% bars: that would read as "spent nothing"
 * rather than "no data available for this month".
 */
export function natureSharesToPercents(shares: NatureShareInput | null): NatureSharePercent[] | null {
  if (shares === null) return null
  return (['fixed', 'variable', 'discretionary', 'unclassified'] as const).map((key) => ({
    key,
    pct: Math.round(shares[key] * 1000) / 10,
  }))
}

/**
 * Design-token color for each nature category. Centralized so no component
 * hard-codes a hex value for these four slices.
 */
export function natureColor(key: NatureKey): string {
  switch (key) {
    case 'fixed':
      return 'var(--chart-1)' // indigo
    case 'variable':
      return 'var(--chart-2)' // violet
    case 'discretionary':
      return 'var(--chart-4)' // amber
    case 'unclassified':
      return 'var(--muted-foreground)'
  }
}

/**
 * Formats a ratio in [0, 1] (e.g. breakeven_discount, ir_rate) as a
 * fixed-decimal percentage string, e.g. 0.0925 -> "9.25%".
 */
export function formatRatioPct(ratio: number, decimals = 2): string {
  return `${(ratio * 100).toFixed(decimals)}%`
}
