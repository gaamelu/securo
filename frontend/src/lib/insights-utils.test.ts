import { describe, expect, it } from 'vitest'
import { formatDelta, parseMoney, statusColor } from './insights-utils'

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
