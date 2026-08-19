import { describe, expect, it } from 'vitest'
import {
  coverageStatus,
  formatDelta,
  formatRatioPct,
  chartDomain,
  natureChartValues,
  natureColor,
  natureSharesToPercents,
  parseMoney,
  projectionDomain,
  statusColor,
} from './insights-utils'

describe('parseMoney', () => {
  it('parses a fixed-2-decimal money string into a number', () => {
    expect(parseMoney('1234.56')).toBe(1234.56)
  })

  it('parses "0.00" as zero', () => {
    expect(parseMoney('0.00')).toBe(0)
  })

  it('parses negative amounts (e.g. negative net worth)', () => {
    expect(parseMoney('-42.00')).toBe(-42)
    expect(parseMoney('-1234.56')).toBe(-1234.56)
  })

  it('returns 0 for null, undefined, and empty string rather than NaN', () => {
    expect(parseMoney(null)).toBe(0)
    expect(parseMoney(undefined)).toBe(0)
    expect(parseMoney('')).toBe(0)
    expect(parseMoney('   ')).toBe(0)
  })

  it('returns 0 for unparseable input rather than NaN', () => {
    expect(parseMoney('not-a-number')).toBe(0)
  })
})

describe('formatDelta', () => {
  it('reports "over" when actual exceeds the reference', () => {
    const result = formatDelta('150.00', '100.00')
    expect(result.status).toBe('over')
    expect(result.sign).toBe('+')
    expect(result.magnitude).toBe(50)
  })

  it('reports "under" when actual is below the reference', () => {
    const result = formatDelta('80.00', '100.00')
    expect(result.status).toBe('under')
    expect(result.sign).toBe('-')
    expect(result.magnitude).toBe(20)
  })

  it('reports "neutral" when actual equals the reference', () => {
    const result = formatDelta('100.00', '100.00')
    expect(result.status).toBe('neutral')
    expect(result.sign).toBe('')
    expect(result.magnitude).toBe(0)
  })

  it('treats missing actual/reference as zero rather than throwing', () => {
    expect(formatDelta(null, '100.00')).toEqual({ sign: '-', magnitude: 100, status: 'under' })
    expect(formatDelta('100.00', null)).toEqual({ sign: '+', magnitude: 100, status: 'over' })
    expect(formatDelta(null, null)).toEqual({ sign: '', magnitude: 0, status: 'neutral' })
  })

  it('handles negative actual values (e.g. net worth deltas)', () => {
    const result = formatDelta('-50.00', '100.00')
    expect(result.status).toBe('under')
    expect(result.magnitude).toBe(150)
  })
})

describe('statusColor', () => {
  it('maps favorable statuses to the emerald token', () => {
    expect(statusColor('under')).toBe('var(--chart-3)')
    expect(statusColor('good')).toBe('var(--chart-3)')
    expect(statusColor('ok')).toBe('var(--chart-3)')
  })

  it('maps unfavorable statuses to the destructive token', () => {
    expect(statusColor('over')).toBe('var(--destructive)')
    expect(statusColor('crit')).toBe('var(--destructive)')
  })

  it('maps warn to the amber chart token', () => {
    expect(statusColor('warn')).toBe('var(--chart-4)')
  })

  it('maps neutral/unknown/no_ref to the muted-foreground token', () => {
    expect(statusColor('neutral')).toBe('var(--muted-foreground)')
    expect(statusColor('unknown')).toBe('var(--muted-foreground)')
    expect(statusColor('no_ref')).toBe('var(--muted-foreground)')
  })
})

describe('coverageStatus', () => {
  it('reports "good" at 90% and above', () => {
    expect(coverageStatus(90)).toBe('good')
    expect(coverageStatus(100)).toBe('good')
  })

  it('reports "warn" between 60% and 90%', () => {
    expect(coverageStatus(60)).toBe('warn')
    expect(coverageStatus(89.9)).toBe('warn')
  })

  it('reports "crit" below 60%', () => {
    expect(coverageStatus(0)).toBe('crit')
    expect(coverageStatus(59.9)).toBe('crit')
  })
})

describe('natureSharesToPercents', () => {
  it('returns null for a month with no shares, never four zeros', () => {
    expect(natureSharesToPercents(null)).toBeNull()
  })

  it('converts fractional shares into rounded 0-100 percentages', () => {
    const result = natureSharesToPercents({
      fixed: 0.4521,
      variable: 0.3,
      discretionary: 0.15,
      unclassified: 0.0979,
    })
    expect(result).toEqual([
      { key: 'fixed', pct: 45.2 },
      { key: 'variable', pct: 30 },
      { key: 'discretionary', pct: 15 },
      { key: 'unclassified', pct: 9.8 },
    ])
  })

  it('handles an all-zero month distinctly from a null (no-data) month', () => {
    const result = natureSharesToPercents({ fixed: 0, variable: 0, discretionary: 0, unclassified: 0 })
    expect(result).toEqual([
      { key: 'fixed', pct: 0 },
      { key: 'variable', pct: 0 },
      { key: 'discretionary', pct: 0 },
      { key: 'unclassified', pct: 0 },
    ])
  })
})

describe('natureChartValues', () => {
  const values = { fixed: '100.00', variable: '25.00', discretionary: '0.00', unclassified: '5.00' }
  const shares = { fixed: 0.5, variable: 0.25, discretionary: 0, unclassified: 0.25 }

  it('keeps absolute amounts for amount mode instead of normalizing every month', () => {
    expect(natureChartValues(values, shares, 'amount')).toEqual([
      { key: 'fixed', value: 100 },
      { key: 'variable', value: 25 },
      { key: 'discretionary', value: 0 },
      { key: 'unclassified', value: 5 },
    ])
  })

  it('converts shares to percentages for participation mode', () => {
    expect(natureChartValues(values, shares, 'share')).toEqual([
      { key: 'fixed', value: 50 },
      { key: 'variable', value: 25 },
      { key: 'discretionary', value: 0 },
      { key: 'unclassified', value: 25 },
    ])
  })

  it('returns null for participation mode when shares are unavailable', () => {
    expect(natureChartValues(values, null, 'share')).toBeNull()
  })
})

describe('chartDomain', () => {
  it('preserves magnitude differences in an absolute chart', () => {
    expect(chartDomain([100, 10000], true)).toEqual({ min: 0, max: 10000 })
  })

  it('includes zero when a projection crosses into negative balance', () => {
    expect(chartDomain([-500, 2000], true)).toEqual({ min: -500, max: 2000 })
  })

  it('expands a flat series to a useful non-zero range', () => {
    expect(chartDomain([0, 0], true)).toEqual({ min: 0, max: 1 })
  })
})

describe('projectionDomain', () => {
  it('includes balance, confidence bounds, and zero', () => {
    expect(projectionDomain([
      { balance: '2000.00', low: '1500.00', high: '2500.00' },
      { balance: '-500.00', low: '-800.00', high: '100.00' },
    ])).toEqual({ min: -800, max: 2500 })
  })
})

describe('natureColor', () => {
  it('returns a distinct design token for each nature key', () => {
    const colors = new Set([
      natureColor('fixed'),
      natureColor('variable'),
      natureColor('discretionary'),
      natureColor('unclassified'),
    ])
    expect(colors.size).toBe(4)
  })
})

describe('formatRatioPct', () => {
  it('formats a fraction as a fixed-decimal percentage', () => {
    expect(formatRatioPct(0.0925)).toBe('9.25%')
  })

  it('supports a custom decimal count', () => {
    expect(formatRatioPct(0.225, 1)).toBe('22.5%')
    expect(formatRatioPct(0.01042, 3)).toBe('1.042%')
  })

  it('handles zero', () => {
    expect(formatRatioPct(0)).toBe('0.00%')
  })
})
