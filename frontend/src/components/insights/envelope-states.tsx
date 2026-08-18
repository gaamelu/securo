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

/** Standard card chrome shared by every Insights block. */
export function InsightsCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm">
      <div className="px-5 pt-5 pb-2">
        <p className="text-sm font-semibold text-foreground">{title}</p>
      </div>
      <div className="px-5 pb-5">{children}</div>
    </div>
  )
}
