// frontend/src/components/insights/VitalsBlock.tsx
//
// GET /api/insights/vitals. Four cards: runway, savings_rate, net_worth,
// credit_utilization. Each card's status/percentages/availability all
// arrive finished from the server — this component only renders, it never
// computes (see house rule in frontend/src/types/insights.ts).
import { useQuery } from '@tanstack/react-query'
import api from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import { insightPeriodLabel, parseMoney, statusColor } from '@/lib/insights-utils'
import { Skeleton } from '@/components/ui/skeleton'
import type { InsightsEnvelope, VitalCard, VitalsData } from '@/types/insights'
import { EnvelopeEmpty, EnvelopeError, InsightsCard } from './envelope-states'

async function fetchVitals(): Promise<InsightsEnvelope<VitalsData>> {
  const { data } = await api.get('/insights/vitals')
  return data
}

export function VitalsBlock() {
  const { data: envelope, isLoading } = useQuery<InsightsEnvelope<VitalsData>>({
    queryKey: ['insights', 'vitals'],
    queryFn: fetchVitals,
  })

  return (
    <InsightsCard title="Sinais vitais">
      {isLoading ? (
        <div className="flex flex-wrap gap-4">
          <Skeleton className="h-24 w-full sm:w-[calc(50%-0.5rem)] lg:w-[calc(25%-0.75rem)]" />
          <Skeleton className="h-24 w-full sm:w-[calc(50%-0.5rem)] lg:w-[calc(25%-0.75rem)]" />
          <Skeleton className="h-24 w-full sm:w-[calc(50%-0.5rem)] lg:w-[calc(25%-0.75rem)]" />
          <Skeleton className="h-24 w-full sm:w-[calc(50%-0.5rem)] lg:w-[calc(25%-0.75rem)]" />
        </div>
      ) : envelope?.error ? (
        <EnvelopeError message={envelope.error.message} />
      ) : !envelope?.data || envelope.data.length === 0 ? (
        <EnvelopeEmpty label="Sem sinais vitais ainda." />
      ) : (
        <VitalsContent cards={envelope.data} currency={envelope.currency} period={insightPeriodLabel(envelope.window, localeForWindow())} />
      )}
    </InsightsCard>
  )
}

const CARD_LABELS: Record<VitalCard['key'], string> = {
  runway: 'Segurança financeira',
  savings_rate: 'Taxa de poupança',
  net_worth: 'Patrimônio líquido',
  credit_utilization: 'Uso do crédito',
}

function formatVitalValue(
  card: VitalCard,
  currency: string,
  locale: string,
  mask: (v: string) => string,
): string {
  if (card.value === null) return '—'
  switch (card.unit) {
    case 'BRL':
      return mask(formatCurrency(parseMoney(card.value), currency, locale))
    case 'months': {
      // Formatting only — the number itself (how many months) is the
      // server's computation, never derived here.
      const n = parseMoney(card.value)
      return `${n.toFixed(1)} meses`
    }
    case 'percent': {
      const n = parseMoney(card.value)
      return `${n.toFixed(1)}%`
    }
    default:
      return card.value
  }
}

function localeForWindow() {
  return 'pt-BR'
}

function VitalsContent({ cards, currency, period }: { cards: VitalsData; currency: string; period: string }) {
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const displayCurrency = currency || user?.preferences?.currency_display || 'USD'
  const locale = useDisplayLocale()

  return (
    <div className="flex flex-col gap-3">
      <p className="text-[11px] text-muted-foreground">Período considerado: {period}. Curvas mostram comportamento histórico; cor da curva indica direção, não substitui estado atual.</p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((card) => (
        <VitalCardView
          key={card.key}
          card={card}
          currency={displayCurrency}
          locale={locale}
          mask={mask}
        />
      ))}
      </div>
    </div>
  )
}

function VitalCardView({
  card,
  currency,
  locale,
  mask,
}: {
  card: VitalCard
  currency: string
  locale: string
  mask: (v: string) => string
}) {
  const label = CARD_LABELS[card.key] ?? card.label

  return (
    <div className="min-w-0 rounded-lg border border-border p-3">
      <p className="text-xs font-medium text-muted-foreground mb-1.5">{label}</p>

      {!card.available ? (
        <div className="flex flex-col gap-1">
          <p className="text-lg font-bold tabular-nums text-muted-foreground">—</p>
          {card.blocked_reason && (
            <p className="text-[11px] text-muted-foreground leading-snug">{card.blocked_reason}</p>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          <div className="flex items-baseline gap-2">
            <p
              className="text-lg font-bold tabular-nums privacy-sensitive"
              style={{ color: statusColor(card.status) }}
            >
              {formatVitalValue(card, currency, locale, mask)}
            </p>
          </div>

          {card.reference && (
            <p className="text-[11px] text-muted-foreground privacy-sensitive">
              Base: {card.reference.label}: {mask(formatCurrency(parseMoney(card.reference.value), currency, locale))}
            </p>
          )}

          {card.series && card.series.length > 1 && (
            <div className="mt-1 flex items-center justify-between gap-2">
              <Sparkline card={card} />
              {card.trend && (
                <div className="min-w-[7rem] text-right text-[11px]" style={{ color: trendColor(card.trend.favorable, card.trend.direction) }}>
                  <p className="font-medium">{trendArrow(card.trend.direction)} {card.trend.label}</p>
                  <p className="text-muted-foreground">variação: {formatTrendDelta(card, card.trend.delta, currency, locale, mask)}</p>
                </div>
              )}
            </div>
          )}

          {/* net_worth carries a blocked_reason even when available: it
              explains the Reserva de Emergência exclusion so this figure
              doesn't silently disagree with the Reports screen. Must be
              visible on the card, not tucked into a tooltip. */}
          {card.blocked_reason && (
            <p className="text-[11px] text-muted-foreground leading-snug">{card.blocked_reason}</p>
          )}
        </div>
      )}
    </div>
  )
}

function formatTrendDelta(card: VitalCard, value: string | null, currency: string, locale: string, mask: (v: string) => string) {
  if (value === null) return '—'
  if (card.unit === 'BRL') return mask(formatCurrency(parseMoney(value), currency, locale))
  return `${parseMoney(value).toFixed(1)}${card.unit === 'percent' ? '%' : ' meses'}`
}

function trendArrow(direction: NonNullable<VitalCard['trend']>['direction']) {
  return direction === 'up' ? '↑' : direction === 'down' ? '↓' : '→'
}

function trendColor(favorable: boolean | null, direction: NonNullable<VitalCard['trend']>['direction']) {
  if (favorable === true) return 'var(--chart-3)'
  if (favorable === false) return 'var(--destructive)'
  return direction === 'stable' ? 'var(--muted-foreground)' : 'var(--chart-4)'
}

function Sparkline({ card }: { card: VitalCard }) {
  const values = card.series?.map((point) => parseMoney(point.value)) ?? []
  const min = Math.min(...values)
  const max = Math.max(...values)
  const points = values.map((value, index) => {
    const x = values.length === 1 ? 0 : (index / (values.length - 1)) * 112
    const y = 24 - ((value - min) / Math.max(0.0001, max - min)) * 20
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const color = trendColor(card.trend?.favorable ?? null, card.trend?.direction ?? 'stable')
  return <svg width="116" height="28" viewBox="0 0 116 28" role="img" aria-label={`Tendência: ${card.trend?.label ?? 'sem referência'}`}><polyline points={points} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /><circle cx={values.length > 1 ? 112 : 0} cy={values.length > 1 ? 24 - ((values[values.length - 1] - min) / Math.max(0.0001, max - min)) * 20 : 24} r="2.5" fill={color} /></svg>
}
