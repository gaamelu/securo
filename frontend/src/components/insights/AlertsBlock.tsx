// frontend/src/components/insights/AlertsBlock.tsx
//
// GET /api/insights/alerts. A ranked, compact list by severity
// (crit > warn > info). title/detail arrive as finished pt-BR text from
// the server — rendered verbatim, never re-worded. This sits at the top of
// the tab, so it stays scannable: no charts, no expandable sections, just
// a severity marker, the message, and an optional amount.
//
// `link.view`/`link.params` (AlertLink) doesn't yet have an established
// client-side routing convention anywhere in this codebase — nav-items.ts
// and the router are owned by another agent working this same branch and
// are off-limits here. Rather than guess a route mapping that might not
// match what gets wired up, the link is rendered as inert context (the
// view name) instead of a clickable navigation target.
import { useQuery } from '@tanstack/react-query'
import api from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import { insightPeriodLabel, parseMoney } from '@/lib/insights-utils'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import type { AlertRow, AlertsData, InsightsEnvelope } from '@/types/insights'
import { EnvelopeEmpty, EnvelopeError, InsightsCard } from './envelope-states'

async function fetchAlerts(): Promise<InsightsEnvelope<AlertsData>> {
  const { data } = await api.get('/insights/alerts')
  return data
}

const SEVERITY_ORDER: Record<AlertRow['severity'], number> = { crit: 0, warn: 1, info: 2 }

const SEVERITY_BADGE: Record<AlertRow['severity'], 'destructive' | 'secondary' | 'outline'> = {
  crit: 'destructive',
  warn: 'secondary',
  info: 'outline',
}

const SEVERITY_LABEL: Record<AlertRow['severity'], string> = {
  crit: 'Crítico',
  warn: 'Atenção',
  info: 'Info',
}

export function AlertsBlock() {
  const { data: envelope, isLoading } = useQuery<InsightsEnvelope<AlertsData>>({
    queryKey: ['insights', 'alerts'],
    queryFn: fetchAlerts,
  })

  return (
    <InsightsCard title="Alertas">
      {isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : envelope?.error ? (
        <EnvelopeError message={envelope.error.message} />
      ) : !envelope?.data || envelope.data.length === 0 ? (
        <EnvelopeEmpty label="Nenhum alerta no momento." />
      ) : (
        <AlertsContent alerts={envelope.data} currency={envelope.currency} period={insightPeriodLabel(envelope.window)} />
      )}
    </InsightsCard>
  )
}

function AlertsContent({ alerts, currency, period }: { alerts: AlertsData; currency: string; period: string }) {
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const displayCurrency = currency || user?.preferences?.currency_display || 'USD'
  const locale = useDisplayLocale()

  const sorted = [...alerts].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity])

  return (
    <div className="flex flex-col gap-2">
      <p className="text-[11px] text-muted-foreground">Período considerado: {period}. Valores à direita mostram a diferença ou impacto indicado pelo alerta.</p>
      <ul className="flex flex-col gap-2">
      {sorted.map((alert) => (
        <li
          key={alert.id}
          className="grid grid-cols-[auto_minmax(0,1fr)_minmax(7rem,auto)] items-start gap-3 rounded-lg border border-border px-3 py-2"
        >
          <Badge variant={SEVERITY_BADGE[alert.severity]} className="mt-0.5 shrink-0">
            {SEVERITY_LABEL[alert.severity]}
          </Badge>

          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-foreground">{alert.title}</p>
            <p className="text-xs text-muted-foreground">{alert.detail}</p>
            {alert.link && (
              <p className="text-[10px] text-muted-foreground/70 mt-0.5">{alert.link.view}</p>
            )}
          </div>

          {alert.amount !== null && (
            <div className="min-w-[7rem] text-right privacy-sensitive">
              <p className="text-sm font-semibold tabular-nums text-foreground">{mask(formatCurrency(parseMoney(alert.amount), displayCurrency, locale))}</p>
              <p className="text-[10px] text-muted-foreground">{amountContext(alert.kind)}</p>
            </div>
          )}
        </li>
      ))}
      </ul>
    </div>
  )
}

function amountContext(kind: AlertRow['kind']) {
  switch (kind) {
    case 'overspend': return 'diferença acima do padrão'
    case 'cash_gap': return 'déficit projetado'
    case 'goal_off_track': return 'aporte necessário'
    default: return 'valor de referência'
  }
}
