// frontend/src/components/insights/CategoriesBlock.tsx
//
// GET /api/insights/categories?reference=budget|historical. Per-category
// actual vs reference, with the budget/historical toggle (owned by the page,
// passed down as a prop) driving the query. INSUFFICIENT_HISTORY is its own
// state, not a generic error — it means "not enough trusted months for the
// historical reference", and the server's message already says so in pt-BR.
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowUpRight } from 'lucide-react'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { insights } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { formatDelta, parseMoney, statusColor } from '@/lib/insights-utils'
import { Skeleton } from '@/components/ui/skeleton'
import type { CategoriesData, CategoryRow, InsightsEnvelope, InsightsReference } from '@/types/insights'
import { EnvelopeEmpty, EnvelopeError, InsightsCard } from './envelope-states'

export function CategoriesBlock({ reference }: { reference: InsightsReference }) {
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const fallbackCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()

  const { data: envelope, isLoading } = useQuery<InsightsEnvelope<CategoriesData>>({
    queryKey: ['insights', 'categories', reference],
    queryFn: () => insights.categories(reference),
  })

  return (
    <InsightsCard
      title="Categorias"
      action={
        <Link
          to="/budgets"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        >
          Configurar orçamentos <ArrowUpRight size={13} aria-hidden="true" />
        </Link>
      }
    >
      <p className="mb-3 text-xs text-muted-foreground">
        Comparação: <span className="font-medium text-foreground">{reference === 'budget' ? 'orçamento mensal' : 'sua mediana histórica'}</span>. Sem referência? <Link to="/budgets" className="underline underline-offset-2 hover:text-foreground">Defina por categoria</Link>.
      </p>
      {isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : envelope?.error?.code === 'INSUFFICIENT_HISTORY' ? (
        <EnvelopeEmpty label={envelope.error.message} />
      ) : envelope?.error ? (
        <EnvelopeError message={envelope.error.message} />
      ) : !envelope?.data ? (
        <EnvelopeEmpty label="Sem dados de categorias ainda." />
      ) : envelope.data.available === false ? (
        <EnvelopeEmpty label={envelope.data.blocked_reason ?? 'Indisponível para este período.'} />
      ) : envelope.data.rows.length === 0 ? (
        <EnvelopeEmpty label="Nenhuma categoria com movimentação neste período." />
      ) : (
        <CategoriesTable rows={envelope.data.rows} mask={mask} currency={envelope.currency || fallbackCurrency} locale={locale} />
      )}
    </InsightsCard>
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
              <th className="pb-2 pl-2 text-right font-medium">Ação</th>
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
                <td className="py-2 pl-2 text-right">
                  {row.status === 'no_ref' ? (
                    <Link to="/budgets" className="text-[11px] font-medium text-primary underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">
                      Definir
                    </Link>
                  ) : null}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
