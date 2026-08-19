// frontend/src/components/insights/ProjectionBlock.tsx
//
// Balance and committed installments answer different questions and therefore
// use separate panels/scales. Hover is a convenience; the table is the
// keyboard/screen-reader equivalent.
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import api from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import { chartDomain, parseMoney, projectionDomain } from '@/lib/insights-utils'
import { Skeleton } from '@/components/ui/skeleton'
import type { InsightsEnvelope, ProjectionData } from '@/types/insights'
import { EnvelopeEmpty, EnvelopeError, EnvelopeRetryError, InsightsCard } from './envelope-states'

async function fetchProjection(): Promise<InsightsEnvelope<ProjectionData>> {
  const { data } = await api.get('/insights/projection')
  return data
}

export function ProjectionBlock() {
  const query = useQuery<InsightsEnvelope<ProjectionData>>({
    queryKey: ['insights', 'projection'],
    queryFn: fetchProjection,
  })

  return (
    <InsightsCard title="Projeção de saldo">
      {query.isLoading && !query.data ? (
        <Skeleton className="h-72 w-full" />
      ) : query.isError && !query.data ? (
        <EnvelopeRetryError message="Não foi possível carregar a projeção de saldo." onRetry={() => query.refetch()} />
      ) : query.data?.error && !query.data.data ? (
        <EnvelopeError message={query.data.error.message} />
      ) : !query.data?.data || query.data.data.points.length === 0 ? (
        <EnvelopeEmpty label="Sem projeção disponível ainda." />
      ) : (
        <ProjectionContent
          data={query.data.data}
          currency={query.data.currency}
          isRefreshing={query.isFetching}
          transportError={query.isError}
          onRetry={() => query.refetch()}
        />
      )}
    </InsightsCard>
  )
}

const BALANCE_HEIGHT = 220
const COMMITTED_HEIGHT = 126
const AXIS_LEFT = 64
const AXIS_BOTTOM = 28
const PLOT_TOP = 14

function ProjectionContent({
  data,
  currency,
  isRefreshing,
  transportError,
  onRetry,
}: {
  data: ProjectionData
  currency: string
  isRefreshing: boolean
  transportError: boolean
  onRetry: () => void
}) {
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const locale = useDisplayLocale()
  const [selectedIndex, setSelectedIndex] = useState(0)
  const displayCurrency = currency || user?.preferences?.currency_display || 'USD'
  const points = data.points
  const width = Math.max(460, AXIS_LEFT + points.length * 64)
  const balanceDomain = useMemo(() => projectionDomain(points), [points])
  const committedDomain = useMemo(() => chartDomain(points.map((point) => parseMoney(point.committed)), true), [points])
  const selected = points[Math.min(selectedIndex, points.length - 1)]
  const xFor = (index: number) => AXIS_LEFT + (index / Math.max(1, points.length - 1)) * (width - AXIS_LEFT - 16)
  const balanceY = (value: number) => scaleY(value, balanceDomain.min, balanceDomain.max, BALANCE_HEIGHT, AXIS_BOTTOM, PLOT_TOP)
  const committedY = (value: number) => scaleY(value, committedDomain.min, committedDomain.max, COMMITTED_HEIGHT, AXIS_BOTTOM, PLOT_TOP)
  const balanceTicks = makeTicks(balanceDomain.min, balanceDomain.max, 4)
  const committedTicks = makeTicks(committedDomain.min, committedDomain.max, 3)
  const zeroBalanceY = balanceY(0)
  const zeroCommittedY = committedY(0)
  const firstProjectedIndex = points.findIndex((point) => point.kind === 'projected')

  const bandPath = (() => {
    const valid = points
      .map((point, index) => ({ point, index }))
      .filter(({ point }) => point.low !== null && point.high !== null && parseMoney(point.low) <= parseMoney(point.high))
    if (valid.length === 0) return null
    const top = valid.map(({ point, index }) => `${xFor(index)},${balanceY(parseMoney(point.high))}`)
    const bottom = valid.slice().reverse().map(({ point, index }) => `${xFor(index)},${balanceY(parseMoney(point.low))}`)
    return `M ${top.join(' L ')} L ${bottom.join(' L ')} Z`
  })()

  const fmt = (value: string | null | undefined) => value === null || value === undefined ? '—' : mask(formatCurrency(parseMoney(value), displayCurrency, locale))
  const fmtAssumption = (value: string) => /^-?\d+(\.\d+)?$/.test(value) ? fmt(value) : value

  return (
    <div className="flex flex-col gap-4">
      {transportError && <EnvelopeRetryError message="Atualização falhou; mostrando dados anteriores." onRetry={onRetry} compact />}
      {isRefreshing && <p className="text-[11px] text-muted-foreground" role="status">Atualizando…</p>}

      <section aria-labelledby="projection-balance-title">
        <h3 id="projection-balance-title" className="mb-1 text-xs font-medium text-muted-foreground">Saldo — real e projetado</h3>
        <div className="privacy-sensitive overflow-x-auto">
          <svg width={width} height={BALANCE_HEIGHT} role="img" aria-label="Saldo real e projetado por mês">
            {balanceTicks.map((tick) => <AxisLine key={tick} x1={AXIS_LEFT} x2={width - 8} y={balanceY(tick)} label={compactMoney(tick, displayCurrency, locale)} />)}
            <line x1={AXIS_LEFT} x2={width - 8} y1={zeroBalanceY} y2={zeroBalanceY} stroke="var(--muted-foreground)" strokeDasharray="3 3" />
            {bandPath && <path d={bandPath} fill="var(--chart-1)" fillOpacity={0.14} stroke="none" />}
            {points.map((point, index) => {
              if (index === 0) return null
              const previous = points[index - 1]
              const negative = parseMoney(previous.balance) < 0 || parseMoney(point.balance) < 0
              return <line key={`balance-line-${point.month}`} x1={xFor(index - 1)} y1={balanceY(parseMoney(previous.balance))} x2={xFor(index)} y2={balanceY(parseMoney(point.balance))} stroke={negative ? 'var(--destructive)' : 'var(--primary)'} strokeWidth={2.5} strokeDasharray={point.kind === 'projected' ? '6 4' : undefined} />
            })}
            {points.map((point, index) => {
              const selected = index === selectedIndex
              const negative = parseMoney(point.balance) < 0
              return (
                <g key={point.month} onMouseEnter={() => setSelectedIndex(index)} onClick={() => setSelectedIndex(index)} opacity={selected ? 1 : 0.9}>
                  <circle cx={xFor(index)} cy={balanceY(parseMoney(point.balance))} r={selected ? 5 : 3.5} fill={negative ? 'var(--destructive)' : point.kind === 'actual' ? 'var(--primary)' : 'var(--card)'} stroke={negative ? 'var(--destructive)' : 'var(--primary)'} strokeWidth={1.5} />
                  {!(point.kind === 'projected' && index > 0 && points[index - 1].month === point.month) && <text x={xFor(index)} y={BALANCE_HEIGHT - 8} textAnchor="middle" className="fill-muted-foreground" style={{ fontSize: 9 }}>{point.month.slice(5)}</text>}
                </g>
              )
            })}
            {firstProjectedIndex > 0 && <line x1={xFor(firstProjectedIndex)} x2={xFor(firstProjectedIndex)} y1={PLOT_TOP} y2={BALANCE_HEIGHT - AXIS_BOTTOM} stroke="var(--border)" strokeDasharray="3 3" />}
          </svg>
        </div>
        <div className="mt-2 flex flex-wrap gap-4 text-[11px] text-muted-foreground">
          <LegendItem color="var(--primary)" label="Saldo real" />
          <LegendItem color="var(--primary)" label="Saldo projetado" dashed />
          <LegendItem color="var(--chart-1)" label="Faixa de confiança" swatch />
          <LegendItem color="var(--destructive)" label="Saldo negativo" />
        </div>
      </section>

      <section aria-labelledby="projection-committed-title">
        <h3 id="projection-committed-title" className="mb-1 text-xs font-medium text-muted-foreground">Parcelas comprometidas — gasto mensal conhecido</h3>
        <div className="privacy-sensitive overflow-x-auto">
          <svg width={width} height={COMMITTED_HEIGHT} role="img" aria-label="Parcelas comprometidas por mês">
            {committedTicks.map((tick) => <AxisLine key={tick} x1={AXIS_LEFT} x2={width - 8} y={committedY(tick)} label={compactMoney(tick, displayCurrency, locale)} />)}
            <line x1={AXIS_LEFT} x2={width - 8} y1={zeroCommittedY} y2={zeroCommittedY} stroke="var(--muted-foreground)" />
            {points.map((point, index) => {
              const value = parseMoney(point.committed)
              const y = committedY(value)
              const height = Math.max(0, zeroCommittedY - y)
              return <rect key={point.month} x={xFor(index) - 10} y={y} width={20} height={height} rx={2} fill="var(--chart-4)" opacity={index === selectedIndex ? 1 : 0.72} onMouseEnter={() => setSelectedIndex(index)} onClick={() => setSelectedIndex(index)} />
            })}
          </svg>
        </div>
      </section>

      <div role="tooltip" className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs">
        <p key={selected.month} className="sr-only" aria-live="polite">{selected.month} selecionado; {selected.kind === 'actual' ? 'saldo real' : 'saldo projetado'}.</p>
        <p className="font-medium text-foreground">{selected.month} · {selected.kind === 'actual' ? 'real' : 'projetado'}</p>
        <div className="mt-1 grid gap-x-4 gap-y-0.5 sm:grid-cols-2">
          <span className="text-muted-foreground">Saldo: <strong className="font-medium text-foreground">{fmt(selected.balance)}</strong></span>
          <span className="text-muted-foreground">Parcelas: <strong className="font-medium text-foreground">{fmt(selected.committed)}</strong></span>
          <span className="text-muted-foreground">Faixa: <strong className="font-medium text-foreground">{selected.low !== null && selected.high !== null ? `${fmt(selected.low)} – ${fmt(selected.high)}` : 'indisponível'}</strong></span>
        </div>
        <div className="mt-1 grid gap-x-4 gap-y-0.5 text-muted-foreground sm:grid-cols-2">
          <span>Renda: {fmt(selected.components?.income_expected)}</span>
          <span>Recorrentes: {fmt(selected.components?.recurring)}</span>
          <span>Parcelas: {fmt(selected.components?.installments)}</span>
          <span>Variável: {fmt(selected.components?.variable_estimate)}</span>
        </div>
      </div>

      <details>
        <summary className="cursor-pointer text-xs font-medium text-muted-foreground">Explorar dados</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-muted-foreground"><th className="pb-1">Mês</th><th className="pb-1 text-right">Tipo</th><th className="pb-1 text-right">Saldo</th><th className="pb-1 text-right">Parcelas</th></tr></thead>
            <tbody>{points.map((point, index) => <tr key={point.month} tabIndex={0} onFocus={() => setSelectedIndex(index)} className="border-t border-border/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"><td className="py-1">{point.month}</td><td className="py-1 text-right">{point.kind === 'actual' ? 'Real' : 'Projetado'}</td><td className="py-1 text-right tabular-nums">{fmt(point.balance)}</td><td className="py-1 text-right tabular-nums">{fmt(point.committed)}</td></tr>)}</tbody>
          </table>
        </div>
      </details>

      {data.assumptions.length > 0 && <div><p className="mb-2 text-xs font-medium text-muted-foreground">Premissas</p><ul className="flex flex-col gap-1">{data.assumptions.map((assumption, index) => <li key={`${assumption.label}-${index}`} className="flex items-center justify-between gap-3 text-xs"><span className="text-foreground">{assumption.label}</span><span className="text-right text-muted-foreground">{fmtAssumption(assumption.value)} <span className="text-[10px]">({assumption.source})</span></span></li>)}</ul></div>}
    </div>
  )
}

function scaleY(value: number, min: number, max: number, height: number, bottom: number, top: number): number {
  return top + (height - bottom - top) - ((value - min) / Math.max(1e-9, max - min)) * (height - bottom - top)
}

function makeTicks(min: number, max: number, count: number): number[] {
  if (count <= 0 || min === max) return [min]
  return Array.from({ length: count + 1 }, (_, index) => min + ((max - min) * index) / count)
}

function compactMoney(value: number, currency: string, locale: string): string {
  return formatCurrency(value, currency, locale).replace(/\s/g, '').replace(/,00$/, '')
}

function AxisLine({ x1, x2, y, label }: { x1: number; x2: number; y: number; label: string }) {
  return <g><line x1={x1} x2={x2} y1={y} y2={y} stroke="var(--border)" /><text x={x1 - 8} y={y + 3} textAnchor="end" className="fill-muted-foreground" style={{ fontSize: 10 }}>{label}</text></g>
}

function LegendItem({ color, label, dashed, swatch }: { color: string; label: string; dashed?: boolean; swatch?: boolean }) {
  return <div className="flex items-center gap-1.5"><svg width={16} height={8} aria-hidden="true">{swatch ? <rect x={0} y={0} width={16} height={8} rx={2} fill={color} fillOpacity={0.4} /> : <line x1={0} y1={4} x2={16} y2={4} stroke={color} strokeWidth={2} strokeDasharray={dashed ? '4 2' : undefined} />}</svg><span>{label}</span></div>
}
