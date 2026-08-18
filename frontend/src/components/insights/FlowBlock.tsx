// frontend/src/components/insights/FlowBlock.tsx
//
// GET /api/insights/flow. Renders the cash-flow Sankey.
//
// Sankey decision: frontend/src/components/reports/CashflowSankey.tsx
// already exists and solves label anti-collision + SVG privacy masking,
// but its props are `{ composition: ReportCompositionItem[], currency,
// locale }` — a flat list of (group, value) pairs that the component
// itself turns into a 2-column-plus-center graph, recomputing totals and
// surplus/deficit from scratch (sums income/expense/investment client-side
// to derive `net`).
//
// FlowData from this endpoint is a DIFFERENT shape: a already-built 3-level
// node/link graph (depth 0 income -> depth 1 group/saved -> depth 2
// category) with deficit already computed server-side
// (backend/app/services/insights_service.py get_flow(), ~line 1408 —
// FlowNode(id="deficit", depth=1, kind="saved", ...)). Squeezing that into
// ReportCompositionItem[] would mean either (a) flattening away the
// group->category hierarchy the backend deliberately built, or (b) faking
// group/expense/investment totals so CashflowSankey's own net/surplus math
// re-derives a number the server already sent — which is exactly the
// "screen never computes" rule this tab is built to avoid. CashflowSankey
// also has no `kind: 'saved'` concept, so "Poupado"/"Saldo"/"Déficit" would
// have nowhere well-defined to render.
//
// So: reuse the *technique*, not the component. This file is a second,
// purpose-built Sankey that reads FlowData directly via d3-sankey, with the
// same anti-collision label placement (ported as pure math into
// lib/insights-flow.ts::computeLabelPositions) and the same SVG privacy
// masking pattern as CashflowSankey. CashflowSankey itself is untouched.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  sankey as d3Sankey,
  sankeyLinkHorizontal,
  sankeyJustify,
} from 'd3-sankey'
import api from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import { parseMoney } from '@/lib/insights-utils'
import {
  buildSankeyGraph,
  computeLabelPositions,
  detectDeficit,
  flowSharePct,
  isFlowEmpty,
  type SankeyGraphLink,
  type SankeyGraphNode,
} from '@/lib/insights-flow'
import { Skeleton } from '@/components/ui/skeleton'
import type { FlowData, InsightsEnvelope } from '@/types/insights'
import { EnvelopeEmpty, EnvelopeError, InsightsCard } from './envelope-states'

async function fetchFlow(): Promise<InsightsEnvelope<FlowData>> {
  const { data } = await api.get('/insights/flow')
  return data
}

const NODE_WIDTH = 16
const NODE_PADDING = 14
const LABEL_MIN_GAP = 26
const TOP_GUTTER = 16

export function FlowBlock() {
  const { data: envelope, isLoading } = useQuery<InsightsEnvelope<FlowData>>({
    queryKey: ['insights', 'flow'],
    queryFn: fetchFlow,
  })

  return (
    <InsightsCard title="Fluxo de caixa">
      {isLoading ? (
        <Skeleton className="h-80 w-full" />
      ) : envelope?.error ? (
        <EnvelopeError message={envelope.error.message} />
      ) : !envelope?.data ? (
        <EnvelopeEmpty label="Sem dados de fluxo ainda." />
      ) : (
        <FlowContent data={envelope.data} currency={envelope.currency} />
      )}
    </InsightsCard>
  )
}

function FlowContent({ data, currency }: { data: FlowData; currency: string }) {
  const { privacyMode, MASK } = usePrivacyMode()
  const { user } = useAuth()
  const displayCurrency = currency || user?.preferences?.currency_display || 'USD'
  const locale = useDisplayLocale()
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const graph = useMemo(() => buildSankeyGraph(data), [data])
  const deficit = useMemo(() => detectDeficit(data), [data])
  const empty = isFlowEmpty(data)

  const maxColumnCount = useMemo(() => {
    const byDepth = new Map<number, number>()
    for (const n of graph.nodes) byDepth.set(n.depth, (byDepth.get(n.depth) ?? 0) + 1)
    return Math.max(1, ...byDepth.values())
  }, [graph.nodes])
  const height = Math.min(720, Math.max(340, maxColumnCount * 56))

  const layout = useMemo(() => {
    if (!graph.hasData || width === 0) return null
    const generator = d3Sankey<SankeyGraphNode, SankeyGraphLink>()
      .nodeWidth(NODE_WIDTH)
      .nodePadding(NODE_PADDING)
      .nodeAlign(sankeyJustify)
      .extent([
        [8, TOP_GUTTER],
        [width - 8, height - 14],
      ])
    return generator({
      nodes: graph.nodes.map((n) => ({ ...n })),
      links: graph.links.map((l) => ({ ...l })),
    })
  }, [graph, width, height])

  const labelY = useMemo(() => {
    if (!layout) return new Map<number, number>()
    const inputs = layout.nodes.map((n, i) => ({
      index: i,
      depth: (n as SankeyGraphNode).depth,
      centerY: ((n.y0 ?? 0) + (n.y1 ?? 0)) / 2,
    }))
    return computeLabelPositions(inputs, LABEL_MIN_GAP)
  }, [layout])

  const totalIncome = useMemo(
    () => parseMoney(data.income_total) || graph.nodes.find((n) => n.kind === 'income')?.numericValue || 0,
    [data.income_total, graph.nodes],
  )

  const fmtAmount = (v: number) =>
    privacyMode ? MASK : formatCurrency(v, displayCurrency, locale)

  if (empty) {
    return <EnvelopeEmpty label="Sem movimentação neste período." />
  }

  const linkPath = sankeyLinkHorizontal<SankeyGraphNode, SankeyGraphLink>()

  return (
    <div className="flex flex-col gap-3">
      {deficit.hasDeficit && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3">
          <p className="text-sm font-semibold text-destructive">Déficit no período</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            Parte do que saiu este mês veio de poupança ou crédito, não de renda:{' '}
            <span className="font-semibold text-destructive privacy-sensitive">
              {fmtAmount(deficit.amount)}
            </span>
          </p>
        </div>
      )}

      <div ref={containerRef} className="w-full privacy-sensitive">
        {layout && (
          <svg width={width} height={height} role="img" aria-label="Diagrama de fluxo de caixa">
            <defs>
              {layout.links.map((link, i) => {
                const src = link.source as SankeyGraphNode & { x1: number }
                const tgt = link.target as SankeyGraphNode & { x0: number }
                return (
                  <linearGradient
                    key={i}
                    id={`insights-flow-grad-${i}`}
                    gradientUnits="userSpaceOnUse"
                    x1={src.x1}
                    x2={tgt.x0}
                  >
                    <stop offset="0%" stopColor={src.color} />
                    <stop offset="100%" stopColor={tgt.color} />
                  </linearGradient>
                )
              })}
            </defs>

            <g fill="none">
              {layout.links.map((link, i) => {
                const value = (link as SankeyGraphLink).value
                const pct = flowSharePct(value, totalIncome)
                return (
                  <path
                    key={i}
                    d={linkPath(link) ?? undefined}
                    stroke={`url(#insights-flow-grad-${i})`}
                    strokeOpacity={0.4}
                    strokeWidth={Math.max(1.5, link.width ?? 1)}
                  >
                    <title>
                      {(link.target as SankeyGraphNode).label}: {fmtAmount(value)} ({pct})
                    </title>
                  </path>
                )
              })}
            </g>

            <g>
              {layout.nodes.map((node, i) => {
                const x0 = node.x0 ?? 0
                const x1 = node.x1 ?? 0
                const y0 = node.y0 ?? 0
                const y1 = node.y1 ?? 0
                const nodeHeight = Math.max(1, y1 - y0)
                const n = node as SankeyGraphNode
                const isLeftmost = n.depth === 0
                const labelX = isLeftmost ? x1 + 8 : x0 - 8
                const ly = labelY.get(i) ?? (y0 + y1) / 2
                const isDeficitNode = n.id === 'deficit'

                return (
                  <g key={i}>
                    <rect
                      x={x0}
                      y={y0}
                      width={Math.max(1, x1 - x0)}
                      height={nodeHeight}
                      fill={n.color}
                      rx={3}
                    >
                      <title>
                        {n.label}: {fmtAmount(n.numericValue)}
                      </title>
                    </rect>
                    <text
                      x={labelX}
                      y={ly}
                      textAnchor={isLeftmost ? 'start' : 'end'}
                      dominantBaseline="middle"
                      className={isDeficitNode ? 'fill-destructive' : 'fill-foreground'}
                      style={{ fontSize: 11, fontWeight: isDeficitNode ? 700 : 500 }}
                    >
                      {n.label}
                      <tspan
                        x={labelX}
                        dy={13}
                        className="fill-muted-foreground"
                        style={{ fontSize: 10, fontFamily: 'var(--font-mono, monospace)' }}
                      >
                        {fmtAmount(n.numericValue)}
                      </tspan>
                    </text>
                  </g>
                )
              })}
            </g>
          </svg>
        )}

        <table className="sr-only">
          <caption>Diagrama de fluxo de caixa</caption>
          <thead>
            <tr>
              <th>Nó</th>
              <th>Valor</th>
            </tr>
          </thead>
          <tbody>
            {graph.nodes.map((n) => (
              <tr key={n.id}>
                <td>{n.label}</td>
                <td>{fmtAmount(n.numericValue)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
