// frontend/src/components/insights/NatureBlock.tsx
//
// Natureza is amount-first. Participation is an explicit alternate view so
// composition never hides the magnitude of spending.
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { insights } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import {
  chartDomain,
  insightMonthLabel,
  insightPeriodLabel,
  natureChartValues,
  natureColor,
  parseMoney,
  type NatureChartMetric,
  type NatureKey,
} from '@/lib/insights-utils'
import { niceTicks } from '@/lib/chart-scale'
import { Skeleton } from '@/components/ui/skeleton'
import type { InsightsEnvelope, NatureData, NatureMonth } from '@/types/insights'
import { EnvelopeEmpty, EnvelopeError, EnvelopeRetryError, InsightsCard } from './envelope-states'

const NATURE_LABELS: Record<NatureKey, string> = {
  fixed: 'Fixo',
  variable: 'Variável',
  discretionary: 'Discricionário',
  unclassified: 'Não classificado',
}
const NATURE_KEYS: NatureKey[] = ['fixed', 'variable', 'discretionary', 'unclassified']
const CHART_HEIGHT = 190
const AXIS_LEFT = 58
const AXIS_BOTTOM = 32
const PLOT_TOP = 12
const BAR_WIDTH = 28
const BAR_GAP = 20

export function NatureBlock() {
  const query = useQuery<InsightsEnvelope<NatureData>>({
    queryKey: ['insights', 'nature'],
    queryFn: () => insights.nature(),
  })

  return (
    <InsightsCard title="Natureza dos gastos">
      {query.isLoading && !query.data ? (
        <Skeleton className="h-64 w-full" />
      ) : query.isError && !query.data ? (
        <EnvelopeRetryError message="Não foi possível carregar a natureza dos gastos." onRetry={() => query.refetch()} />
      ) : query.data?.error && !query.data.data ? (
        <EnvelopeError message={query.data.error.message} />
      ) : !query.data?.data ? (
        <EnvelopeEmpty label="Sem dados de natureza ainda." />
      ) : query.data.data.series.length === 0 ? (
        <EnvelopeEmpty label="Nenhum mês com movimentação neste período." />
      ) : (
        <NatureContent
          series={query.data.data.series}
          savingsDestination={query.data.data.savings_destination}
          period={insightPeriodLabel(query.data.window)}
          currency={query.data.currency}
          isRefreshing={query.isFetching}
          transportError={query.isError}
          onRetry={() => query.refetch()}
        />
      )}
    </InsightsCard>
  )
}

function NatureContent({
  series,
  savingsDestination,
  period,
  currency,
  isRefreshing,
  transportError,
  onRetry,
}: {
  series: NatureMonth[]
  savingsDestination: NatureData['savings_destination']
  period: string
  currency: string
  isRefreshing: boolean
  transportError: boolean
  onRetry: () => void
}) {
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const locale = useDisplayLocale()
  const [metric, setMetric] = useState<NatureChartMetric>('amount')
  const [selectedMonth, setSelectedMonth] = useState<string | null>(null)
  const displayCurrency = currency || user?.preferences?.currency_display || 'USD'
  const width = Math.max(440, AXIS_LEFT + series.length * (BAR_WIDTH + BAR_GAP) + 18)

  const domain = useMemo(() => {
    const values = series.flatMap((month) => {
      const chartValues = natureChartValues(month.values, month.shares, metric)
      return chartValues?.map((item) => item.value) ?? []
    })
    return metric === 'share' ? { min: 0, max: 100 } : chartDomain(values, true)
  }, [metric, series])
  const ticks = niceTicks(domain.min, domain.max, 4)
  const selected = series.find((month) => month.month === selectedMonth) ?? series[series.length - 1]

  const yFor = (value: number) =>
    PLOT_TOP + (CHART_HEIGHT - PLOT_TOP - AXIS_BOTTOM) -
    ((value - domain.min) / Math.max(1e-9, domain.max - domain.min)) *
      (CHART_HEIGHT - PLOT_TOP - AXIS_BOTTOM)
  const xFor = (index: number) => AXIS_LEFT + index * (BAR_WIDTH + BAR_GAP)
  const formatValue = (value: number) =>
    metric === 'share' ? `${value.toFixed(1)}%` : mask(formatCurrency(value, displayCurrency, locale))
  const formatCell = (month: NatureMonth, key: NatureKey) => {
    if (metric === 'share' && month.shares === null) return '—'
    return formatValue(metric === 'amount' ? parseMoney(month.values[key]) : (month.shares?.[key] ?? 0) * 100)
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-[11px] text-muted-foreground">Período considerado: {period}. Participação soma 100% do gasto classificado no mês; mês sem gasto aparece como indisponível.</p>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-3" aria-label="Legenda de natureza">
          {NATURE_KEYS.map((key) => (
            <div key={key} className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: natureColor(key) }} aria-hidden="true" />
              {NATURE_LABELS[key]}
            </div>
          ))}
          {series.some((month) => !month.trusted) && <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm border border-muted-foreground/60" style={{ backgroundImage: 'repeating-linear-gradient(135deg, transparent 0 2px, currentColor 2px 3px)' }} aria-hidden="true" />
            Fora da janela confiável
          </div>}
        </div>
        <div className="inline-flex rounded-md border border-border bg-card p-0.5" role="group" aria-label="Métrica do gráfico">
          {(['amount', 'share'] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setMetric(option)}
              aria-pressed={metric === option}
              className={`rounded px-2 py-1 text-[11px] font-medium transition-colors ${metric === option ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'}`}
            >
              {option === 'amount' ? 'Valor' : 'Participação'}
            </button>
          ))}
        </div>
      </div>

      {transportError && (
        <EnvelopeRetryError message="Atualização falhou; mostrando dados anteriores." onRetry={onRetry} compact />
      )}
      {isRefreshing && <p className="text-[11px] text-muted-foreground" role="status">Atualizando…</p>}

      <div className="privacy-sensitive overflow-x-auto">
        <svg
          width={width}
          height={CHART_HEIGHT}
          role="img"
          aria-label={`Natureza dos gastos por mês em ${metric === 'amount' ? 'valores' : 'participação percentual'}`}
        >
          <defs>
            <pattern id="nature-untrusted-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="6" stroke="var(--foreground)" strokeOpacity="0.24" strokeWidth="2" />
            </pattern>
          </defs>
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={AXIS_LEFT} x2={width - 8} y1={yFor(tick)} y2={yFor(tick)} stroke="var(--border)" />
              <text x={AXIS_LEFT - 8} y={yFor(tick) + 3} textAnchor="end" className="fill-muted-foreground" style={{ fontSize: 10 }}>
                {metric === 'share' ? `${tick.toFixed(0)}%` : compactMoney(tick, displayCurrency, locale)}
              </text>
            </g>
          ))}
          {series.map((month, index) => {
            const x = xFor(index)
            const chartValues = natureChartValues(month.values, month.shares, metric)
            const isSelected = month.month === selected?.month
            let cursorY = yFor(0)
            return (
              <g
                key={month.month}
                onMouseEnter={() => setSelectedMonth(month.month)}
                onFocus={() => setSelectedMonth(month.month)}
                onClick={() => setSelectedMonth(month.month)}
                className="cursor-pointer"
                opacity={isSelected ? 1 : 0.9}
              >
                {chartValues === null ? (
                  <rect x={x} y={yFor(100)} width={BAR_WIDTH} height={yFor(0) - yFor(100)} fill="var(--muted)" rx={3}>
                    <title>{`${month.month}: participação indisponível`}</title>
                  </rect>
                ) : (
                  chartValues.map(({ key, value }) => {
                    const nextY = yFor(value)
                    const height = Math.max(0, cursorY - nextY)
                    const segment = (
                      <rect key={key} x={x} y={cursorY - height} width={BAR_WIDTH} height={height} fill={natureColor(key)}>
                        <title>{`${month.month} · ${NATURE_LABELS[key]}: ${formatValue(metric === 'amount' ? parseMoney(month.values[key]) : value)}`}</title>
                      </rect>
                    )
                    cursorY -= height
                    return segment
                  })
                )}
                {!month.trusted && <rect x={x} y={yFor(domain.max)} width={BAR_WIDTH} height={Math.max(0, yFor(domain.min) - yFor(domain.max))} fill="url(#nature-untrusted-hatch)" rx={3} />}
                <text x={x + BAR_WIDTH / 2} y={CHART_HEIGHT - 8} textAnchor="middle" className="fill-muted-foreground" style={{ fontSize: 9 }}>
                  {insightMonthLabel(month.month).replace(' de ', ' ')}
                </text>
                {isSelected && <rect x={x - 2} y={yFor(domain.max)} width={BAR_WIDTH + 4} height={Math.max(0, yFor(domain.min) - yFor(domain.max))} fill="none" stroke="var(--ring)" strokeDasharray="2 2" rx={4} />}
              </g>
            )
          })}
        </svg>
      </div>

      <div role="tooltip" className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs">
        <p key={`${selected.month}-${metric}`} className="sr-only" aria-live="polite">
          {insightMonthLabel(selected.month)}; {metric === 'amount' ? 'valores' : 'participação'} selecionados.
        </p>
        <p className="font-medium text-foreground">{insightMonthLabel(selected.month)}{selected.trusted ? '' : ' · fora da janela confiável'}</p>
        <div className="mt-1 grid grid-cols-2 gap-x-5 gap-y-0.5 sm:grid-cols-4">
          {NATURE_KEYS.map((key) => (
            <span key={key} className="text-muted-foreground">
              {NATURE_LABELS[key]}: <strong className="font-medium text-foreground">{formatCell(selected, key)}</strong>
            </span>
          ))}
        </div>
        {metric === 'share' && selected.shares === null && <p className="mt-1 text-muted-foreground">Participação indisponível neste mês; altere para Valor.</p>}
      </div>

      {savingsDestination && (
        <div className="flex items-center gap-2 rounded-md border border-chart-3/30 bg-chart-3/5 px-3 py-2 text-xs">
          <span className="h-2.5 w-2.5 rounded-sm bg-chart-3" aria-hidden="true" />
          <span className="text-muted-foreground">Destino de poupança (contas savings):</span>
          <strong className="text-foreground">{mask(formatCurrency(parseMoney(savingsDestination.amount), displayCurrency, locale))}</strong>
          <span className="text-muted-foreground">({savingsDestination.account_count} conta{savingsDestination.account_count === 1 ? '' : 's'})</span>
        </div>
      )}

      <details>
        <summary className="cursor-pointer text-xs font-medium text-muted-foreground">Explorar dados</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-muted-foreground"><th className="pb-1">Mês</th>{NATURE_KEYS.map((key) => <th key={key} className="pb-1 text-right">{NATURE_LABELS[key]}</th>)}</tr></thead>
            <tbody>{series.map((month) => <tr key={month.month} tabIndex={0} className="border-t border-border/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring" onFocus={() => setSelectedMonth(month.month)}><td className="py-1">{insightMonthLabel(month.month)}</td>{NATURE_KEYS.map((key) => <td key={key} className="py-1 text-right tabular-nums">{formatCell(month, key)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      </details>
    </div>
  )
}

function compactMoney(value: number, currency: string, locale: string): string {
  return formatCurrency(value, currency, locale).replace(/\s/g, '').replace(/,00$/, '')
}
