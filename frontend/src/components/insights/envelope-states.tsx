// frontend/src/components/insights/envelope-states.tsx
//
// Shared rendering for the three envelope states every Insights block must
// distinguish (see insights.tsx's original EnvelopeError/EnvelopeEmpty,
// extracted here so HygieneBlock/CategoriesBlock/NatureBlock/BreakevenBlock
// can all reuse the same markup instead of re-implementing it):
//  - `data` present            -> block renders its own content
//  - `error` present           -> render the server's finished pt-BR message verbatim
//  - `data` absent, no `error` -> render an "empty" state (not an error)

/** Renders a finished, server-authored error message verbatim — never re-worded. */
export function EnvelopeError({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3">
      <p className="text-sm text-destructive">{message}</p>
    </div>
  )
}

/** Renders an empty (not error) state — no data yet, distinct from a failure. */
export function EnvelopeEmpty({ label }: { label: string }) {
  return <p className="text-muted-foreground text-sm text-center py-10">{label}</p>
}

/** Transport failure, distinct from a server-authored envelope error. */
export function EnvelopeRetryError({
  message,
  onRetry,
  compact = false,
}: {
  message: string
  onRetry: () => void
  compact?: boolean
}) {
  return (
    <div className={`flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 ${compact ? 'px-3 py-2' : 'px-4 py-3'}`}>
      <p className="text-sm text-destructive">{message}</p>
      <button type="button" onClick={onRetry} className="shrink-0 rounded-md border border-destructive/30 px-2.5 py-1 text-xs font-medium text-destructive hover:bg-destructive/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring">
        Tentar novamente
      </button>
    </div>
  )
}

/** Standard card chrome shared by every Insights block. */
export function InsightsCard({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm">
      <div className="flex items-center justify-between gap-3 px-5 pt-5 pb-2">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {action}
      </div>
      <div className="px-5 pb-5">{children}</div>
    </div>
  )
}
