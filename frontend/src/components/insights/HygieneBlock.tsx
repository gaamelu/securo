// frontend/src/components/insights/HygieneBlock.tsx
//
// GET /api/insights/hygiene. Three coverage bars (categorized, nature_set,
// transfers_paired) rendered from the server's own `pct` — never
// recomputed from done/total — plus the top-5 worst offenders ranked by
// amount. The offenders are the actionable part of this block: one large
// uncategorized transfer matters more than fifty small ones, so amount is
// the prominent figure, not the missing-count.
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { insights } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { coverageStatus, formatPct, parseMoney, statusColor } from '@/lib/insights-utils'
import { Skeleton } from '@/components/ui/skeleton'
import type { HygieneData, InsightsEnvelope } from '@/types/insights'
import { EnvelopeEmpty, EnvelopeError, InsightsCard } from './envelope-states'

export function HygieneBlock() {
  const { data: envelope, isLoading } = useQuery<InsightsEnvelope<HygieneData>>({
    queryKey: ['insights', 'hygiene'],
    queryFn: () => insights.hygiene(),
  })

  return (
    <InsightsCard title="Higiene dos dados">
      {isLoading ? (
        <div className="flex gap-4">
          <Skeleton className="h-16 w-28" />
          <Skeleton className="h-16 w-28" />
          <Skeleton className="h-16 w-28" />
        </div>
      ) : envelope?.error ? (
        <EnvelopeError message={envelope.error.message} />
      ) : !envelope?.data ? (
        <EnvelopeEmpty label="Sem dados de higiene ainda." />
      ) : (
        <HygieneContent data={envelope.data} />
      )}
    </InsightsCard>
  )
}

function HygieneContent({ data }: { data: HygieneData }) {
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()

  const stats = [
    { key: 'categorized', label: 'Categorizadas', stat: data.coverage.categorized },
    { key: 'nature_set', label: 'Natureza definida', stat: data.coverage.nature_set },
    { key: 'transfers_paired', label: 'Transferências pareadas', stat: data.coverage.transfers_paired },
  ] as const

  // Worst offenders sorted by amount (descending), capped to the top 5 —
  // the block's job is to surface what's worth fixing first, and one large
  // amount outranks many small ones.
  const topOffenders = [...data.worst_offenders]
    .sort((a, b) => parseMoney(b.amount) - parseMoney(a.amount))
    .slice(0, 5)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-6">
        {stats.map(({ key, label, stat }) => {
          const status = coverageStatus(stat.pct)
          return (
            <div key={key} className="min-w-0 flex-1 min-w-[140px]">
              <p className="text-xs font-medium text-muted-foreground mb-1">{label}</p>
              <div className="flex items-baseline gap-2 mb-1">
                <p className="text-lg font-bold tabular-nums" style={{ color: statusColor(status) }}>
                  {formatPct(stat.pct)}
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {stat.done}/{stat.total}
                </p>
              </div>
              <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full transition-[width]"
                  style={{ width: `${Math.min(100, Math.max(0, stat.pct))}%`, backgroundColor: statusColor(status) }}
                />
              </div>
            </div>
          )
        })}
      </div>

      {topOffenders.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-2">Piores ofensores</p>
          <div className="privacy-sensitive flex flex-col gap-1.5">
            {topOffenders.map((o, i) => (
              <div key={`${o.kind}-${o.label}-${i}`} className="flex items-center justify-between gap-3 text-xs">
                <span className="text-foreground truncate">{o.label}</span>
                <div className="flex items-center gap-3 shrink-0">
                  <span className="text-muted-foreground">
                    {o.missing} pendente{o.missing === 1 ? '' : 's'}
                  </span>
                  <span className="font-semibold tabular-nums text-foreground min-w-[80px] text-right">
                    {mask(formatCurrency(parseMoney(o.amount), currency, locale))}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
