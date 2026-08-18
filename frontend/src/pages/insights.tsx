import { useTranslation } from 'react-i18next'
import { PageHeader } from '@/components/page-header'

// Placeholder for the Insights module. Real content lands in a later step.
export default function InsightsPage() {
  const { t } = useTranslation()

  return (
    <div>
      <PageHeader section={t('nav.insights')} title={t('nav.insights')} />
    </div>
  )
}
