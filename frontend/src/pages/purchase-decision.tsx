import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { PageHeader } from '@/components/page-header'
import { InsightsCard } from '@/components/insights/envelope-states'
import { insights } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { BreakevenBlock } from '@/components/insights/BreakevenBlock'
import type { PurchaseDecisionData } from '@/types/insights'

export default function PurchaseDecisionPage() {
  const [price, setPrice] = useState('5000')
  const [discount, setDiscount] = useState('3')
  const [installments, setInstallments] = useState('12')
  const mutation = useMutation({
    mutationFn: () => insights.purchaseDecision({
      price: Number(price).toFixed(2),
      cash_discount_pct: Number(discount),
      installments: Number(installments),
    }),
  })

  return (
    <div>
      <PageHeader section="Ferramentas" title="Decisão de compra" />
      <p className="mb-5 max-w-3xl text-sm text-muted-foreground">
        Simule à vista versus parcelado. Resultado considera rendimento e impacto no caixa; não altera Insights.
      </p>
      <InsightsCard title="Simulador">
        <form className="grid gap-3 sm:grid-cols-3" onSubmit={(event) => { event.preventDefault(); mutation.mutate() }}>
          <label className="grid gap-1 text-xs text-muted-foreground">Preço<input className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} /></label>
          <label className="grid gap-1 text-xs text-muted-foreground">Desconto à vista %<input className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground" inputMode="decimal" value={discount} onChange={(e) => setDiscount(e.target.value)} /></label>
          <label className="grid gap-1 text-xs text-muted-foreground">Parcelas<input className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground" type="number" min="1" max="24" value={installments} onChange={(e) => setInstallments(e.target.value)} /></label>
          <button className="rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground sm:col-span-3 sm:w-fit" type="submit" disabled={mutation.isPending}>Calcular</button>
        </form>
        {mutation.isError && <p className="mt-3 text-sm text-destructive">Não foi possível calcular. Revise os valores.</p>}
        {mutation.data?.data && <DecisionResult data={mutation.data.data} />}
      </InsightsCard>
      <div className="mt-5"><BreakevenBlock /></div>
    </div>
  )
}

function DecisionResult({ data }: { data: PurchaseDecisionData }) {
  const { user } = useAuth()
  const { mask } = usePrivacyMode()
  const locale = useDisplayLocale()
  const currency = user?.preferences?.currency_display ?? 'BRL'
  return (
    <div className="mt-4 grid gap-4 lg:grid-cols-2">
      <div className="rounded-lg border border-chart-3/30 bg-chart-3/10 p-4">
        <p className="text-lg font-bold text-foreground">{data.verdict.headline}</p>
        <p className="mt-1 text-xs text-muted-foreground">Ponto de equilíbrio: {(data.verdict.breakeven_discount * 100).toFixed(2)}% · ganho líquido: {mask(formatCurrency(Number(data.verdict.net_gain), currency, locale))}</p>
      </div>
      <details className="rounded-lg border border-border p-4">
        <summary className="cursor-pointer text-sm font-semibold">Ver fluxo mensal</summary>
        <div className="mt-2 overflow-x-auto"><table className="w-full text-xs"><tbody>{data.schedule.map((row) => <tr key={row.month} className="border-t border-border/50"><td className="py-1">{row.month}</td><td className="py-1 text-right">{mask(formatCurrency(Number(row.payment), currency, locale))}</td><td className="py-1 text-right">{mask(formatCurrency(Number(row.yield_net), currency, locale))}</td></tr>)}</tbody></table></div>
      </details>
    </div>
  )
}
