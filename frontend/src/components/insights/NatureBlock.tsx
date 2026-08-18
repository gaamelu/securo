// frontend/src/components/insights/NatureBlock.tsx
//
// GET /api/insights/nature?months=12. The fixed/variable/discretionary/
// unclassified split over the month series, drawn as a stacked bar per
// month (inline SVG, consistent with CashflowSankey's approach). A month
// whose `shares` is null had no trusted spend — it renders as "sem dados",
// never as four 0% segments, since that would misrepresent "no data" as
// "spent nothing everywhere".
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { insights } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import {
  natureColor,
  natureSharesToPercents,
  parseMoney,
  type NatureKey,
} from '@/lib/insights-utils'
import { Skeleton } from '@/components/ui/skeleton'
import type { InsightsEnvelope, NatureData, NatureMonth } from '@/types/insights'
import { EnvelopeEmpty, EnvelopeError, InsightsCard } from './envelope-states'

const NATURE_LABELS: Record<NatureKey, string> = {
  fixed: 'Fixo',
  variable: 'Variável',
  discretionary: 'Discricionário',
  unclassified: 'Não classificado',
}

const NATURE_KEYS: NatureKey[] = ['fixed', 'variable', 'discretionary', 'unclassified']

const BAR_WIDTH = 28
const BAR_GAP = 10
const CHART_HEIGHT = 120

export function NatureBlock() {
  const { data: envelope, isLoading } = useQuery<InsightsEnvelope<NatureData>>({
    queryKey: ['insights', 'nature'],
    queryFn: () => insights.nature(),
  })

  return (
    <InsightsCard title="Natureza dos gastos">
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : envelope?.error ? (
        <EnvelopeError message={envelope.error.message} />
      ) : !envelope?.data ? (
        <EnvelopeEmpty label="Sem dados de natureza ainda." />
      ) : envelope.data.series.length === 0 ? (
        <EnvelopeEmpty label="Nenhum mês com movimentação neste período." />
      ) : (
        <NatureContent series={envelope.data.series} />
      )}
    </InsightsCard>
  )
}

function NatureContent({ series }: { series: NatureMonth[] }) {
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()

  const width = series.length * (BAR_WIDTH + BAR_GAP)

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-3">
        {NATURE_KEYS.map((key) => (
          <div key={key} className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ backgroundColor: natureColor(key) }} />
            {NATURE_LABELS[key]}
          </div>
        ))}
      </div>

      <div className="privacy-sensitive overflow-x-auto">
        <svg
          width={Math.max(width, 240)}
          height={CHART_HEIGHT + 24}
          role="img"
          aria-label="Distribuição da natureza dos gastos por mês"
        >
          {series.map((month, i) => {
            const x = i * (BAR_WIDTH + BAR_GAP)
            const percents = natureSharesToPercents(month.shares)

            if (percents === null) {
              return (
                <g key={month.month}>
                  <rect
                    x={x}
                    y={0}
                    width={BAR_WIDTH}
                    height={CHART_HEIGHT}
                    fill="var(--muted)"
                    rx={3}
                  >
                    <title>{`${month.month}: sem dados`}</title>
                  </rect>
                  <text
                    x={x + BAR_WIDTH / 2}
                    y={CHART_HEIGHT / 2}
                    textAnchor="middle"
                    fontSize={9}
                    fill="var(--muted-foreground)"
                    transform={`rotate(-90 ${x + BAR_WIDTH / 2} ${CHART_HEIGHT / 2})`}
                  >
                    sem dados
                  </text>
                  <text
                    x={x + BAR_WIDTH / 2}
                    y={CHART_HEIGHT + 16}
                    textAnchor="middle"
                    fontSize={9}
                    fill="var(--muted-foreground)"
                  >
                    {month.month}
                  </text>
                </g>
              )
            }

            let cumY = CHART_HEIGHT
            return (
              <g key={month.month}>
                {percents.map(({ key, pct }) => {
                  const segHeight = (pct / 100) * CHART_HEIGHT
                  cumY -= segHeight
                  const amount = month.values[key]
                  return (
                    <rect
                      key={key}
                      x={x}
                      y={cumY}
                      width={BAR_WIDTH}
                      height={segHeight}
                      fill={natureColor(key)}
                    >
                      <title>
                        {`${month.month} · ${NATURE_LABELS[key]}: ${pct}% (${mask(
                          formatCurrency(parseMoney(amount), currency, locale),
                        )})`}
                      </title>
                    </rect>
                  )
                })}
                <text
                  x={x + BAR_WIDTH / 2}
                  y={CHART_HEIGHT + 16}
                  textAnchor="middle"
                  fontSize={9}
                  fill="var(--muted-foreground)"
                >
                  {month.month}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
    </div>
  )
}
