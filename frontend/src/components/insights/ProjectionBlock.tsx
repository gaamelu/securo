// frontend/src/components/insights/ProjectionBlock.tsx
//
// GET /api/insights/projection. Balance-over-time chart: actual points then
// projected points, with a low/high confidence band on the projected leg.
// `committed` (installments already contracted) is fact, not estimate — it
// is drawn with a solid, heavier line and its own legend entry, distinct
// from the variable `low`/`high` band which is genuinely uncertain.
// `assumptions` is rendered as a plain list exactly as the server sends it
// (label/value/source) — this component never explains or derives why a
// number is what it is.
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import api from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import { parseMoney } from '@/lib/insights-utils'
import { Skeleton } from '@/components/ui/skeleton'
import type { InsightsEnvelope, ProjectionData } from '@/types/insights'
import { EnvelopeEmpty, EnvelopeError, InsightsCard } from './envelope-states'

async function fetchProjection(): Promise<InsightsEnvelope<ProjectionData>> {
  const { data } = await api.get('/insights/projection')
  return data
}

export function ProjectionBlock() {
  const { data: envelope, isLoading } = useQuery<InsightsEnvelope<ProjectionData>>({
    queryKey: ['insights', 'projection'],
    queryFn: fetchProjection,
  })

  return (
    <InsightsCard title="Projeção de saldo">
      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : envelope?.error ? (
        <EnvelopeError message={envelope.error.message} />
      ) : !envelope?.data || envelope.data.points.length === 0 ? (
        <EnvelopeEmpty label="Sem projeção disponível ainda." />
      ) : (
        <ProjectionContent data={envelope.data} currency={envelope.currency} />
      )}
    </InsightsCard>
  )
}

const CHART_HEIGHT = 220
const CHART_PADDING_X = 12
const CHART_PADDING_Y = 16

function ProjectionContent({ data, currency }: { data: ProjectionData; currency: string }) {
  const { privacyMode, MASK } = usePrivacyMode()
  const { user } = useAuth()
  const displayCurrency = currency || user?.preferences?.currency_display || 'USD'
  const locale = useDisplayLocale()

  const points = data.points
  const width = Math.max(360, points.length * 64)

  const { minY, maxY } = useMemo(() => {
    let min = Infinity
    let max = -Infinity
    for (const p of points) {
      const values = [parseMoney(p.balance)]
      if (p.low !== null) values.push(parseMoney(p.low))
      if (p.high !== null) values.push(parseMoney(p.high))
      for (const v of values) {
        if (v < min) min = v
        if (v > max) max = v
      }
    }
    if (min === Infinity) {
      min = 0
      max = 0
    }
    if (min === max) {
      min -= 1
      max += 1
    }
    return { minY: min, maxY: max }
  }, [points])

  const xFor = (i: number) =>
    CHART_PADDING_X + (i / Math.max(1, points.length - 1)) * (width - 2 * CHART_PADDING_X)
  const yFor = (v: number) =>
    CHART_HEIGHT -
    CHART_PADDING_Y -
    ((v - minY) / (maxY - minY)) * (CHART_HEIGHT - 2 * CHART_PADDING_Y)

  const balancePath = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i)} ${yFor(parseMoney(p.balance))}`)
    .join(' ')

  const bandPath = useMemo(() => {
    const withBand = points
      .map((p, i) => ({ i, p }))
      .filter(({ p }) => p.low !== null && p.high !== null)
    if (withBand.length === 0) return null
    const top = withBand.map(({ i, p }) => `${xFor(i)},${yFor(parseMoney(p.high))}`)
    const bottom = withBand
      .slice()
      .reverse()
      .map(({ i, p }) => `${xFor(i)},${yFor(parseMoney(p.low))}`)
    return `M ${top.join(' L ')} L ${bottom.join(' L ')} Z`
  }, [points, minY, maxY, width])

  const fmtAmount = (v: number) =>
    privacyMode ? MASK : formatCurrency(v, displayCurrency, locale)

  const firstProjectedIdx = points.findIndex((p) => p.kind === 'projected')

  return (
    <div className="flex flex-col gap-4">
      <div className="w-full overflow-x-auto privacy-sensitive">
        <svg width={width} height={CHART_HEIGHT} role="img" aria-label="Gráfico de projeção de saldo">
          {bandPath && (
            <path d={bandPath} fill="var(--chart-1)" fillOpacity={0.12} stroke="none" />
          )}

          {/* Divider marking where actuals end and projection begins. */}
          {firstProjectedIdx > 0 && (
            <line
              x1={xFor(firstProjectedIdx)}
              x2={xFor(firstProjectedIdx)}
              y1={CHART_PADDING_Y}
              y2={CHART_HEIGHT - CHART_PADDING_Y}
              stroke="var(--border)"
              strokeDasharray="3 3"
            />
          )}

          {/* Balance line: solid for actuals, dashed for projected. */}
          {points.map((p, i) => {
            if (i === 0) return null
            const prev = points[i - 1]
            return (
              <line
                key={`bal-${i}`}
                x1={xFor(i - 1)}
                y1={yFor(parseMoney(prev.balance))}
                x2={xFor(i)}
                y2={yFor(parseMoney(p.balance))}
                stroke="var(--primary)"
                strokeWidth={2}
                strokeDasharray={p.kind === 'projected' ? '5 3' : undefined}
              />
            )
          })}

          {/* Committed (installments already contracted) — fact, not
              estimate, so it gets its own heavier solid line rather than
              blending into the projected balance. */}
          {points.map((p, i) => {
            if (i === 0) return null
            const prev = points[i - 1]
            const committedPrev = parseMoney(prev.committed)
            const committedCur = parseMoney(p.committed)
            if (committedPrev === 0 && committedCur === 0) return null
            return (
              <line
                key={`committed-${i}`}
                x1={xFor(i - 1)}
                y1={yFor(committedPrev)}
                x2={xFor(i)}
                y2={yFor(committedCur)}
                stroke="var(--chart-4)"
                strokeWidth={3}
              />
            )
          })}

          {points.map((p, i) => (
            <g key={p.month}>
              <circle
                cx={xFor(i)}
                cy={yFor(parseMoney(p.balance))}
                r={3.5}
                fill={p.kind === 'actual' ? 'var(--primary)' : 'var(--card)'}
                stroke="var(--primary)"
                strokeWidth={1.5}
              >
                <title>
                  {p.month}: {fmtAmount(parseMoney(p.balance))}
                </title>
              </circle>
              <text
                x={xFor(i)}
                y={CHART_HEIGHT - 2}
                textAnchor="middle"
                className="fill-muted-foreground"
                style={{ fontSize: 9 }}
              >
                {p.month}
              </text>
            </g>
          ))}
        </svg>
      </div>

      <div className="flex flex-wrap gap-4 text-[11px] text-muted-foreground">
        <LegendItem color="var(--primary)" label="Saldo (real)" solid />
        <LegendItem color="var(--primary)" label="Saldo (projetado)" dashed />
        <LegendItem color="var(--chart-1)" label="Faixa de confiança" swatch />
        <LegendItem color="var(--chart-4)" label="Parcelas comprometidas" solid thick />
      </div>

      {data.assumptions.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-2">Premissas</p>
          <ul className="flex flex-col gap-1">
            {data.assumptions.map((a, i) => (
              <li key={`${a.label}-${i}`} className="flex items-center justify-between gap-3 text-xs">
                <span className="text-foreground">{a.label}</span>
                <span className="text-muted-foreground text-right">
                  {a.value} <span className="text-[10px]">({a.source})</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function LegendItem({
  color,
  label,
  solid,
  dashed,
  swatch,
  thick,
}: {
  color: string
  label: string
  solid?: boolean
  dashed?: boolean
  swatch?: boolean
  thick?: boolean
}) {
  return (
    <div className="flex items-center gap-1.5">
      {swatch ? (
        <span className="inline-block h-2.5 w-4 rounded-sm" style={{ backgroundColor: color, opacity: 0.4 }} />
      ) : (
        <svg width={16} height={8}>
          <line
            x1={0}
            y1={4}
            x2={16}
            y2={4}
            stroke={color}
            strokeWidth={thick ? 3 : 2}
            strokeDasharray={dashed ? '4 2' : undefined}
          />
        </svg>
      )}
      <span>{label}</span>
    </div>
  )
}

