// frontend/src/pages/insights.tsx
//
// Shell for the Insights tab. Four blocks are live end-to-end today
// (hygiene, categories, nature, breakeven-table); the remaining five from
// the design (alerts, vitals, flow/Sankey, projection, goals) are still
// being written and are rendered here as clearly-marked placeholders, not
// fetched — owned by other agents working the same branch.
//
// Structure mirrors frontend/src/pages/reports.tsx: useQuery for data
// fetching per block, a PageHeader, explicit loading/error/empty states.
// Every monetary value goes through usePrivacyMode's `mask`, per the
// dashboard's established pattern.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { PageHeader } from '@/components/page-header'
import { BreakevenBlock } from '@/components/insights/BreakevenBlock'
import { CategoriesBlock } from '@/components/insights/CategoriesBlock'
import { HygieneBlock } from '@/components/insights/HygieneBlock'
import { NatureBlock } from '@/components/insights/NatureBlock'
import type { InsightsReference } from '@/types/insights'

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
  { key: 'projection', title: 'Projeção', endpoint: 'GET /api/insights/projection' },
  { key: 'goals', title: 'Metas', endpoint: 'GET /api/insights/goals' },
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

        <NatureBlock />
        <CategoriesBlock reference={reference} />
        <HygieneBlock />
        <BreakevenBlock />

        <PlaceholderBlock title={PLACEHOLDER_BLOCKS[4].title} endpoint={PLACEHOLDER_BLOCKS[4].endpoint} />
      </div>
    </div>
  )
}
