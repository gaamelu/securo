// frontend/src/pages/insights.tsx
//
// Foundation shell for the Insights tab. Only two backend endpoints exist
// today (`/api/insights/hygiene` and `/api/insights/categories`); the other
// seven blocks from the design (alerts, vitals, flow, nature, projection,
// goals, breakeven/purchase-decision) are still being written server-side
// and are rendered here as clearly-marked placeholders, not fetched.
//
// Structure mirrors frontend/src/pages/reports.tsx: useQuery for data
// fetching, a PageHeader, explicit loading/error/empty states. Every
// monetary value goes through usePrivacyMode's `mask`, per the dashboard's
// established pattern (24 call sites there).
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { insights } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { parseMoney, formatDelta, statusColor } from '@/lib/insights-utils'
import { PageHeader } from '@/components/page-header'
import { Skeleton } from '@/components/ui/skeleton'
import type {
  CategoriesData,
  CategoryRow,
  HygieneData,
  InsightsEnvelope,
  InsightsReference,
} from '@/types/insights'

// ---------------------------------------------------------------------------
// Shared envelope-state rendering
// ---------------------------------------------------------------------------

/**
 * The envelope can express three genuinely distinct states, and the design
 * is explicit that they must render differently:
 *  - `data` present            -> render the block's content
 *  - `error` present           -> render the server's pt-BR message as-is
 *  - `data` absent, no `error` -> render an "empty" state (not an error)
 *
 * `error.message` is finished, user-facing pt-BR text from the server —
 * never re-word or re-translate it.
 */
function EnvelopeError({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3">
      <p className="text-sm text-destructive">{message}</p>
    </div>
  )
}

function EnvelopeEmpty({ label }: { label: string }) {
  return (
    <p className="text-muted-foreground text-sm text-center py-10">{label}</p>
  )
}

// ---------------------------------------------------------------------------
// Block: Hygiene (GET /api/insights/hygiene)
// ---------------------------------------------------------------------------

function HygieneBlock() {
  const { data: envelope, isLoading } = useQuery<InsightsEnvelope<HygieneData>>({
    queryKey: ['insights', 'hygiene'],
    queryFn: () => insights.hygiene(),
  })

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm">
      <div className="px-5 pt-5 pb-2">
        <p className="text-sm font-semibold text-foreground">Higiene dos dados</p>
      </div>
      <div className="px-5 pb-5">
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
      </div>
    </div>
  )
}

function HygieneContent({ data }: { data: HygieneData }) {
  const stats = [
    { key: 'categorized', label: 'Categorizadas', stat: data.coverage.categorized },
    { key: 'nature_set', label: 'Natureza definida', stat: data.coverage.nature_set },
    { key: 'transfers_paired', label: 'Transferências pareadas', stat: data.coverage.transfers_paired },
  ] as const

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-6">
        {stats.map(({ key, label, stat }) => {
          const status = stat.pct >= 90 ? 'good' : stat.pct >= 60 ? 'warn' : 'crit'
          return (
            <div key={key} className="min-w-0">
              <p className="text-xs font-medium text-muted-foreground mb-0.5">{label}</p>
              <p className="text-lg font-bold tabular-nums" style={{ color: statusColor(status) }}>
                {stat.pct.toFixed(1)}%
              </p>
              <p className="text-[11px] text-muted-foreground">
                {stat.done}/{stat.total}
              </p>
            </div>
          )
        })}
      </div>

      {data.worst_offenders.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-2">Piores ofensores</p>
          <div className="flex flex-col gap-1.5">
            {data.worst_offenders.map((o, i) => (
              <div key={`${o.kind}-${o.label}-${i}`} className="flex items-center justify-between text-xs">
                <span className="text-foreground truncate">{o.label}</span>
                <span className="text-muted-foreground shrink-0 ml-3">
                  {o.missing} pendente{o.missing === 1 ? '' : 's'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Block: Categories (GET /api/insights/categories?reference=budget|historical)
// ---------------------------------------------------------------------------

function CategoriesBlock({ reference }: { reference: InsightsReference }) {
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()

  const { data: envelope, isLoading } = useQuery<InsightsEnvelope<CategoriesData>>({
    queryKey: ['insights', 'categories', reference],
    queryFn: () => insights.categories(reference),
  })

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm">
      <div className="px-5 pt-5 pb-2">
        <p className="text-sm font-semibold text-foreground">Categorias</p>
      </div>
      <div className="px-5 pb-5">
        {isLoading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : envelope?.error ? (
          <EnvelopeError message={envelope.error.message} />
        ) : !envelope?.data ? (
          <EnvelopeEmpty label="Sem dados de categorias ainda." />
        ) : envelope.data.available === false ? (
          <EnvelopeEmpty label={envelope.data.blocked_reason ?? 'Indisponível para este período.'} />
        ) : envelope.data.rows.length === 0 ? (
          <EnvelopeEmpty label="Nenhuma categoria com movimentação neste período." />
        ) : (
          <CategoriesTable rows={envelope.data.rows} mask={mask} currency={userCurrency} locale={locale} />
        )}
      </div>
    </div>
  )
}

function CategoriesTable({
  rows,
  mask,
  currency,
  locale,
}: {
  rows: CategoryRow[]
  mask: (value: string) => string
  currency: string
  locale: string
}) {
  return (
    <div className="privacy-sensitive overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[11px] text-muted-foreground uppercase tracking-wider">
            <th className="pb-2 font-medium">Categoria</th>
            <th className="pb-2 font-medium text-right">Valor</th>
            <th className="pb-2 font-medium text-right">Referência</th>
            <th className="pb-2 font-medium text-right">Diferença</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const delta = formatDelta(row.amount, row.reference)
            return (
              <tr key={row.category_id ?? row.label} className="border-t border-border/50">
                <td className="py-2">
                  <div className="flex items-center gap-1.5">
                    <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: row.color }} />
                    <span className="text-foreground truncate">{row.label}</span>
                  </div>
                </td>
                <td className="py-2 text-right tabular-nums text-foreground">
                  {mask(formatCurrency(parseMoney(row.amount), currency, locale))}
                </td>
                <td className="py-2 text-right tabular-nums text-muted-foreground">
                  {row.reference ? mask(formatCurrency(parseMoney(row.reference), currency, locale)) : '—'}
                </td>
                <td
                  className="py-2 text-right tabular-nums font-medium"
                  style={{ color: row.status === 'no_ref' ? statusColor('unknown') : statusColor(row.status) }}
                >
                  {row.status === 'no_ref'
                    ? '—'
                    : mask(`${delta.sign}${formatCurrency(delta.magnitude, currency, locale)}`)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Placeholder blocks — endpoints not written yet, deliberately not fetched.
// ---------------------------------------------------------------------------

function PlaceholderBlock({ title, endpoint }: { title: string; endpoint: string }) {
  return (
    <div className="bg-card rounded-xl border border-dashed border-border shadow-sm">
      <div className="px-5 pt-5 pb-2">
        <p className="text-sm font-semibold text-foreground">{title}</p>
      </div>
      <div className="px-5 pb-5">
        <p className="text-xs text-muted-foreground">
          Em construção — aguardando <code className="font-mono">{endpoint}</code> no backend.
        </p>
      </div>
    </div>
  )
}

const PLACEHOLDER_BLOCKS = [
  { key: 'alerts', title: 'Alertas', endpoint: 'GET /api/insights/alerts' },
  { key: 'vitals', title: 'Sinais vitais', endpoint: 'GET /api/insights/vitals' },
  { key: 'flow', title: 'Fluxo (Sankey)', endpoint: 'GET /api/insights/flow' },
  { key: 'nature', title: 'Natureza dos gastos', endpoint: 'GET /api/insights/nature' },
  { key: 'projection', title: 'Projeção', endpoint: 'GET /api/insights/projection' },
  { key: 'goals', title: 'Metas', endpoint: 'GET /api/insights/goals' },
  { key: 'breakeven', title: 'Tabela de equilíbrio', endpoint: 'GET /api/insights/breakeven-table' },
] as const

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function InsightsPage() {
  const { t } = useTranslation()
  const [reference, setReference] = useState<InsightsReference>('budget')

  return (
    <div>
      <PageHeader
        section={t('nav.insights')}
        title={t('nav.insights')}
        action={
          <div className="flex items-center rounded-lg border border-border bg-card overflow-hidden">
            {(['budget', 'historical'] as const).map((opt) => (
              <button
                key={opt}
                type="button"
                onClick={() => setReference(opt)}
                aria-pressed={reference === opt}
                className={`px-3 py-1.5 text-xs font-semibold transition-colors ${
                  reference === opt
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                }`}
              >
                {opt === 'budget' ? 'Orçamento' : 'Histórico'}
              </button>
            ))}
          </div>
        }
      />

      <div className="flex flex-col gap-5">
        <PlaceholderBlock title={PLACEHOLDER_BLOCKS[0].title} endpoint={PLACEHOLDER_BLOCKS[0].endpoint} />
        <PlaceholderBlock title={PLACEHOLDER_BLOCKS[1].title} endpoint={PLACEHOLDER_BLOCKS[1].endpoint} />
        <PlaceholderBlock title={PLACEHOLDER_BLOCKS[2].title} endpoint={PLACEHOLDER_BLOCKS[2].endpoint} />
        <PlaceholderBlock title={PLACEHOLDER_BLOCKS[3].title} endpoint={PLACEHOLDER_BLOCKS[3].endpoint} />
        <PlaceholderBlock title={PLACEHOLDER_BLOCKS[4].title} endpoint={PLACEHOLDER_BLOCKS[4].endpoint} />

        <CategoriesBlock reference={reference} />
        <HygieneBlock />

        <PlaceholderBlock title={PLACEHOLDER_BLOCKS[5].title} endpoint={PLACEHOLDER_BLOCKS[5].endpoint} />
        <PlaceholderBlock title={PLACEHOLDER_BLOCKS[6].title} endpoint={PLACEHOLDER_BLOCKS[6].endpoint} />
      </div>
    </div>
  )
}
