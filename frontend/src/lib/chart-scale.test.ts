import { describe, expect, it } from 'vitest'
import { niceTicks } from './chart-scale'

describe('niceTicks', () => {
  it('uses round multiples and includes zero for cash charts', () => {
    expect(niceTicks(-1240, 6840, 5)).toEqual([-2000, 0, 2000, 4000, 6000, 8000])
  })

  it('keeps small ranges readable', () => {
    expect(niceTicks(0, 47, 4)).toEqual([0, 20, 40, 60])
  })
})
