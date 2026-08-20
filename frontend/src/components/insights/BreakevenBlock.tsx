// frontend/src/components/insights/BreakevenBlock.tsx
//
// GET /api/insights/breakeven-table?monthly_yield=0.01042. Read-only
// reference table: for each installment count n, the IR rate, net monthly
// yield, breakeven discount, and gain per R$1000. The breakeven_discount
// column is the point of this block — it answers "what cash discount beats
// paying in N installments instead of investing the cash".
import { useQuery } from '@tanstack/react-query'
import { insights } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatRatioPct, parseMoney } from '@/lib/insights-utils'
import { Skeleton } from '@/components/ui/skeleton'
import type { BreakevenTableData, InsightsEnvelope } from '@/types/insights'
import { EnvelopeEmpty, EnvelopeError, InsightsCard } from './envelope-states'

const DEFAULT_MONTHLY_YIELD = 0.01042

export function BreakevenBlock() {
  const { data: envelope, isLoading } = useQuery<InsightsEnvelope<BreakevenTableData>>({
    queryKey: ['insights', 'breakeven-table', DEFAULT_MONTHLY_YIELD],
    queryFn: () => insights.breakevenTable(DEFAULT_MONTHLY_YIELD),
  })

  return (
    <InsightsCard title="Tabela de equilíbrio">
      {isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-7 w-full" />
          ))}
        </div>
      ) : envelope?.error?.code === 'NO_YIELD_BASIS' ? (
        <EnvelopeEmpty label={envelope.error.message} />
      ) : envelope?.error ? (
        <EnvelopeError message={envelope.error.message} />
      ) : !envelope?.data ? (
        <EnvelopeEmpty label="Sem dados de rendimento ainda." />
      ) : (
        <BreakevenContent data={envelope.data} />
      )}
    </InsightsCard>
  )
}

function BreakevenContent({ data }: { data: BreakevenTableData }) {
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const { yield_basis, rows } = data

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground">
        Rendimento mensal:{' '}
        <span className="font-medium text-foreground">{formatRatioPct(Number(yield_basis.monthly_gross), 3)}</span>
        {' · '}amostra de {yield_basis.sample_size} obs. · {yield_basis.window_days.min}-{yield_basis.window_days.max}{' '}
        dias
      </p>

      <div className="privacy-sensitive overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] text-muted-foreground uppercase tracking-wider">
              <th className="pb-2 font-medium">Parcelas</th>
              <th className="pb-2 font-medium text-right">IR</th>
              <th className="pb-2 font-medium text-right">Líquido/mês</th>
              <th className="pb-2 font-medium text-right">Desconto de equilíbrio</th>
              <th className="pb-2 font-medium text-right">Ganho por R$1000</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.n} className="border-t border-border/50">
                <td className="py-1.5 text-foreground tabular-nums">{row.n}x</td>
                <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                  {formatRatioPct(row.ir_rate, 1)}
                </td>
                <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                  {formatRatioPct(Number(row.net_monthly), 3)}
                </td>
                <td className="py-1.5 text-right tabular-nums font-semibold text-foreground">
                  {formatRatioPct(row.breakeven_discount, 2)}
                </td>
                <td className="py-1.5 text-right tabular-nums text-foreground">
                  {mask(formatCurrency(parseMoney(row.gain_per_1000), currency, locale))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
