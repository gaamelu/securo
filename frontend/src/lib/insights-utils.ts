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
