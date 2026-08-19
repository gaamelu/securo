// frontend/src/components/insights/GoalsBlock.tsx
//
// GET /api/insights/goals. Per-goal progress, observed contribution, and
// estimated completion. `progress` arrives as a finished server-computed
// ratio — never derived here. A `stalled` goal with a null
// `estimated_completion` is real information ("this goal is not moving"),
// not a formatting gap: it renders an explicit "parada" state instead of a
// blank date or an em dash, per the house rule that the tab must never
// let missing-but-meaningful data collapse into "—".
import { useQuery } from '@tanstack/react-query'
import api from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import { parseMoney, statusColor } from '@/lib/insights-utils'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import type { GoalRow, GoalsData, InsightsEnvelope } from '@/types/insights'
import { EnvelopeEmpty, EnvelopeError, InsightsCard } from './envelope-states'

async function fetchGoals(): Promise<InsightsEnvelope<GoalsData>> {
  const { data } = await api.get('/insights/goals')
  return data
}

export function GoalsBlock() {
  const { data: envelope, isLoading } = useQuery<InsightsEnvelope<GoalsData>>({
    queryKey: ['insights', 'goals'],
    queryFn: fetchGoals,
  })

  return (
    <InsightsCard title="Metas">
      {isLoading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : envelope?.error ? (
        <EnvelopeError message={envelope.error.message} />
      ) : !envelope?.data || envelope.data.length === 0 ? (
        <EnvelopeEmpty label="Nenhuma meta cadastrada ainda." />
      ) : (
        <GoalsContent goals={envelope.data} currency={envelope.currency} />
      )}
    </InsightsCard>
  )
}

const STATUS_LABEL: Record<GoalRow['status'], string> = {
  on_track: 'No caminho',
  behind: 'Atrasada',
  stalled: 'Parada',
  unknown: 'Sem dados suficientes',
}

function GoalsContent({ goals, currency }: { goals: GoalsData; currency: string }) {
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const displayCurrency = currency || user?.preferences?.currency_display || 'USD'
  const locale = useDisplayLocale()

  return (
    <div className="flex flex-col gap-3">
      {goals.map((goal) => (
        <GoalRowView
          key={goal.goal_id}
          goal={goal}
          currency={displayCurrency}
          locale={locale}
          mask={mask}
        />
      ))}
    </div>
  )
}

function GoalRowView({
  goal,
  currency,
  locale,
  mask,
}: {
  goal: GoalRow
  currency: string
  locale: string
  mask: (v: string) => string
}) {
  const pct = Math.min(100, Math.max(0, goal.progress * 100))
  const badgeVariant =
    goal.status === 'on_track' ? 'default' : goal.status === 'behind' ? 'destructive' : 'secondary'

  return (
    <div className="rounded-lg border border-border p-3">
      <div className="flex items-center justify-between gap-3 mb-2">
        <p className="text-sm font-medium text-foreground truncate">{goal.label}</p>
        <Badge variant={badgeVariant}>{STATUS_LABEL[goal.status]}</Badge>
      </div>

      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden mb-1.5">
        <div
          className="h-full rounded-full transition-[width]"
          style={{ width: `${pct}%`, backgroundColor: statusColor(goal.status === 'unknown' ? 'unknown' : goal.status === 'on_track' ? 'good' : goal.status === 'behind' ? 'warn' : 'crit') }}
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs">
        <span className="text-muted-foreground privacy-sensitive">
          {mask(formatCurrency(parseMoney(goal.current), currency, locale))} de{' '}
          {mask(formatCurrency(parseMoney(goal.target), currency, locale))}
        </span>

        {goal.observed_contribution !== null && (
          <span className="text-muted-foreground privacy-sensitive">
            Aporte observado: {mask(formatCurrency(parseMoney(goal.observed_contribution), currency, locale))}/mês
          </span>
        )}

        <CompletionLabel goal={goal} />
      </div>

      {goal.blocked_reason && (
        <p className="text-[11px] text-muted-foreground leading-snug mt-1.5">{goal.blocked_reason}</p>
      )}
    </div>
  )
}

function CompletionLabel({ goal }: { goal: GoalRow }) {
  if (goal.estimated_completion !== null) {
    return <span className="text-muted-foreground">Conclusão estimada: {goal.estimated_completion}</span>
  }

  // A stalled goal with no estimated_completion is meaningful — the goal
  // has stopped moving, not merely "unknown". Say so explicitly rather
  // than rendering an empty date or a bare "—".
  if (goal.status === 'stalled') {
    return <span className="font-medium" style={{ color: statusColor('crit') }}>Meta parada — sem contribuição recente</span>
  }

  return <span className="text-muted-foreground">Conclusão estimada: sem dados suficientes</span>
}
