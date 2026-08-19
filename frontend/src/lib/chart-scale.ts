export function niceTicks(min: number, max: number, targetCount = 5): number[] {
  const rawMin = Math.min(min, 0)
  const rawMax = Math.max(max, 0)
  if (rawMin === rawMax) return [rawMin]
  const span = rawMax - rawMin
  const rough = span / Math.max(1, targetCount)
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const normalized = rough / magnitude
  const step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude
  const start = Math.floor(rawMin / step) * step
  const end = Math.ceil(rawMax / step) * step
  const ticks: number[] = []
  for (let value = start; value <= end + step / 100; value += step) {
    ticks.push(Number(value.toFixed(10)))
  }
  return ticks
}
