// frontend/src/lib/insights-flow.ts
//
// Pure helpers for the Insights "Flow" (Sankey) block. Kept dependency-free
// and DOM-free on purpose: vitest.config.ts runs with `environment: 'node'`
// (no jsdom, no testing-library anywhere in this project), so any logic that
// needs a unit test has to live here as a plain function rather than inside
// a component.
//
// GET /api/insights/flow already returns a finished node/link graph
// (FlowData: nodes depth 0/1/2, kind income|group|category|saved) — the
// backend has done the tree-building and deficit computation
// (backend/app/services/insights_service.py get_flow(), ~line 1408). This
// file does NOT recompute any of that; it only reshapes the wire format
// into what d3-sankey needs (numeric node indices instead of string ids)
// and provides small pure formatting/detection helpers so the component
// stays declarative.
import { parseMoney } from './insights-utils'
import type { FlowData, FlowLink, FlowNode } from '../types/insights'

// ---------------------------------------------------------------------------
// d3-sankey input shapes
// ---------------------------------------------------------------------------

/**
 * A FlowNode plus the numeric value d3-sankey needs to lay out node height.
 *
 * `value` is deliberately omitted from the inherited shape: on the wire it is
 * a money string, while d3-sankey writes its own numeric `value` onto every
 * node during layout. Keeping both under one name makes the type unsatisfiable
 * (`string` is not assignable to `number`), so the wire value is preserved as
 * `moneyValue` and the parsed one as `numericValue`.
 */
export interface SankeyGraphNode extends Omit<FlowNode, 'value'> {
  /** Parsed once here so downstream d3 layout code never re-parses money strings. */
  numericValue: number
  /** The original wire string, for display through the privacy mask. */
  moneyValue: string
}

/** A FlowLink resolved to numeric node indices, as d3-sankey requires. */
export interface SankeyGraphLink {
  source: number
  target: number
  value: number
  /** Original ids, kept for tooltips/titles without a second lookup. */
  sourceId: string
  targetId: string
}

export interface SankeyGraph {
  nodes: SankeyGraphNode[]
  links: SankeyGraphLink[]
  hasData: boolean
}

/**
 * Converts the wire FlowData (string ids, string money) into the numeric
 * node/link graph d3-sankey's `sankey()` generator expects. This is a pure
 * reshape — no totals are recomputed, no deficit is inferred; every number
 * comes from parsing a money string the server already produced.
 *
 * Nodes/links referencing an id not present in `nodes` are dropped rather
 * than thrown on, so a partially-malformed payload degrades to "draw what's
 * resolvable" instead of crashing the block.
 */
export function buildSankeyGraph(data: FlowData): SankeyGraph {
  const nodes = data.nodes ?? []
  const rawLinks = data.links ?? []

  if (nodes.length === 0) {
    return { nodes: [], links: [], hasData: false }
  }

  const indexOf = new Map<string, number>()
  nodes.forEach((n, i) => indexOf.set(n.id, i))

  const graphNodes: SankeyGraphNode[] = nodes.map(({ value, ...rest }) => ({
    ...rest,
    numericValue: parseMoney(value),
    moneyValue: value,
  }))

  const graphLinks: SankeyGraphLink[] = []
  for (const link of rawLinks) {
    const s = indexOf.get(link.source)
    const t = indexOf.get(link.target)
    if (s === undefined || t === undefined) continue
    graphLinks.push({
      source: s,
      target: t,
      value: parseMoney(link.value),
      sourceId: link.source,
      targetId: link.target,
    })
  }

  return { nodes: graphNodes, links: graphLinks, hasData: graphLinks.length > 0 }
}

// ---------------------------------------------------------------------------
// Deficit detection
// ---------------------------------------------------------------------------

export interface DeficitInfo {
  /** True when the month's outflow was funded partly from savings/credit, not income. */
  hasDeficit: boolean
  /** Parsed deficit amount, 0 when absent or non-positive. */
  amount: number
  /** The `saved`-kind node carrying the deficit, if the graph has one (id === 'deficit' by backend convention). */
  node: FlowNode | null
}

/**
 * Detects whether a flow graph has a non-zero deficit. `FlowData.deficit` is
 * optional on the wire (the empty-month fixture omits it) so this treats
 * null/undefined/"0.00" all as "no deficit" rather than distinguishing them
 * — the block only needs to know whether to show the deficit callout.
 *
 * Falls back to scanning for a `kind: 'saved'` node whose id is `deficit`
 * when the top-level field is absent but the node graph carries one anyway,
 * since the backend emits both in lockstep (see get_flow(), FlowNode(id="deficit", ...)).
 */
export function detectDeficit(data: FlowData): DeficitInfo {
  const topLevel = parseMoney(data.deficit)
  if (topLevel > 0) {
    const node = (data.nodes ?? []).find((n) => n.id === 'deficit') ?? null
    return { hasDeficit: true, amount: topLevel, node }
  }

  const deficitNode = (data.nodes ?? []).find((n) => n.kind === 'saved' && n.id === 'deficit')
  if (deficitNode) {
    const amount = parseMoney(deficitNode.value)
    if (amount > 0) {
      return { hasDeficit: true, amount, node: deficitNode }
    }
  }

  return { hasDeficit: false, amount: 0, node: null }
}

// ---------------------------------------------------------------------------
// Label placement (anti-collision), ported from the depth-based layout
// CashflowSankey.tsx uses for its two-column case, generalized to
// FlowData's three depths (0/1/2) instead of a fixed left/right split.
// ---------------------------------------------------------------------------

export interface LabelPositionInput {
  index: number
  depth: number
  centerY: number
}

/**
 * Given each node's ideal vertical center (from the d3-sankey layout) and
 * its depth column, returns an adjusted y per node index so that labels in
 * the same column never sit closer than `minGap` — pushing later ones (by
 * ascending ideal position) further down. Pure math, no DOM measurement:
 * mirrors CashflowSankey's per-column collision pass but grouped by `depth`
 * instead of a left/right boolean, since Flow has three columns.
 */
export function computeLabelPositions(
  inputs: LabelPositionInput[],
  minGap: number,
): Map<number, number> {
  const positions = new Map<number, number>()
  const byDepth = new Map<number, LabelPositionInput[]>()

  for (const item of inputs) {
    const list = byDepth.get(item.depth) ?? []
    list.push(item)
    byDepth.set(item.depth, list)
  }

  for (const list of byDepth.values()) {
    const sorted = [...list].sort((a, b) => a.centerY - b.centerY)
    let prev = -Infinity
    for (const item of sorted) {
      let y = item.centerY
      if (y < prev + minGap) y = prev + minGap
      positions.set(item.index, y)
      prev = y
    }
  }

  return positions
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

/**
 * Formats a flow value as compact currency, or returns the privacy mask
 * verbatim when privacy mode is on. Mirrors CashflowSankey's `fmtAmount`
 * so the two Sankeys read identically when placed side by side.
 */
export function formatFlowAmount(
  value: number,
  currency: string,
  locale: string,
  privacyMode: boolean,
  mask: string,
): string {
  if (privacyMode) return mask
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}

/**
 * Percentage of `value` relative to `total`, formatted to 1 decimal with a
 * trailing "%". Returns "0%" when total is 0 or negative rather than NaN/Infinity.
 */
export function flowSharePct(value: number, total: number): string {
  if (total <= 0) return '0%'
  return `${((value / total) * 100).toFixed(1)}%`
}

/**
 * True when the graph has no meaningful data to draw — either no nodes at
 * all, or every node's parsed value is zero (a month with rows but nothing
 * flowing through them). Used to choose the empty state over an svg that
 * would render as a blank rectangle.
 */
export function isFlowEmpty(data: FlowData): boolean {
  const nodes = data.nodes ?? []
  if (nodes.length === 0) return true
  return nodes.every((n) => parseMoney(n.value) === 0)
}

/** Re-exported for tests/components that only need link-level helpers. */
export type { FlowData, FlowLink, FlowNode }
