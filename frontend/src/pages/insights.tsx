// frontend/src/pages/insights.tsx
//
// Shell for the Insights tab. All nine blocks are live end-to-end.
//
// Structure mirrors frontend/src/pages/reports.tsx: useQuery for data
// fetching per block, a PageHeader, explicit loading/error/empty states.
// Every monetary value goes through usePrivacyMode's `mask`, per the
// dashboard's established pattern.
//
// Block order follows the design: alerts first because they are the only
// part that asks for action, then the vitals summary, then the flow and
// per-category detail behind it, then the forward-looking blocks, and
// finally the reference material that rarely changes.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { PageHeader } from '@/components/page-header'
import { AlertsBlock } from '@/components/insights/AlertsBlock'
import { BreakevenBlock } from '@/components/insights/BreakevenBlock'
import { CategoriesBlock } from '@/components/insights/CategoriesBlock'
import { FlowBlock } from '@/components/insights/FlowBlock'
import { GoalsBlock } from '@/components/insights/GoalsBlock'
import { HygieneBlock } from '@/components/insights/HygieneBlock'
import { NatureBlock } from '@/components/insights/NatureBlock'
import { ProjectionBlock } from '@/components/insights/ProjectionBlock'
import { VitalsBlock } from '@/components/insights/VitalsBlock'
import type { InsightsReference } from '@/types/insights'

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
        <AlertsBlock />
        <VitalsBlock />
        <FlowBlock />
        <NatureBlock />
        <CategoriesBlock reference={reference} />
        <ProjectionBlock />
        <GoalsBlock />
        <HygieneBlock />
        <BreakevenBlock />
      </div>
    </div>
  )
}
