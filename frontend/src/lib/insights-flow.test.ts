import { describe, expect, it } from 'vitest'
import {
  buildSankeyGraph,
  computeLabelPositions,
  detectDeficit,
  flowSharePct,
  formatFlowAmount,
  isFlowEmpty,
} from './insights-flow'
import type { FlowData } from '@/types/insights'

const emptyFlow: FlowData = {
  nodes: [],
  links: [],
  collapse_threshold: '0.00',
  income_total: null,
  deficit: null,
}

const zeroValueFlow: FlowData = {
  nodes: [
    { id: 'renda', label: 'Renda', depth: 0, kind: 'income', color: '#22C55E', value: '0.00' },
  ],
  links: [],
  collapse_threshold: '0.00',
  income_total: '0.00',
  deficit: null,
}

const simpleFlow: FlowData = {
  nodes: [
    { id: 'renda', label: 'Renda', depth: 0, kind: 'income', color: '#22C55E', value: '5000.00' },
    { id: 'grp-moradia', label: 'Moradia', depth: 1, kind: 'group', color: '#F59E0B', value: '2000.00' },
    { id: 'cat-aluguel', label: 'Aluguel', depth: 2, kind: 'category', color: '#F59E0B', value: '2000.00' },
    { id: 'poupado', label: 'Poupado', depth: 1, kind: 'saved', color: '#0EA5E9', value: '1000.00' },
    { id: 'saldo', label: 'Saldo', depth: 1, kind: 'saved', color: '#A3E635', value: '2000.00' },
  ],
  links: [
    { source: 'renda', target: 'grp-moradia', value: '2000.00' },
    { source: 'grp-moradia', target: 'cat-aluguel', value: '2000.00' },
    { source: 'renda', target: 'poupado', value: '1000.00' },
    { source: 'renda', target: 'saldo', value: '2000.00' },
  ],
  collapse_threshold: '50.00',
  income_total: '5000.00',
  deficit: null,
}

const deficitFlow: FlowData = {
  nodes: [
    { id: 'renda', label: 'Renda', depth: 0, kind: 'income', color: '#22C55E', value: '1000.00' },
    { id: 'grp-moradia', label: 'Moradia', depth: 1, kind: 'group', color: '#F59E0B', value: '1500.00' },
    { id: 'cat-aluguel', label: 'Aluguel', depth: 2, kind: 'category', color: '#F59E0B', value: '1500.00' },
    { id: 'deficit', label: 'Déficit', depth: 1, kind: 'saved', color: '#EF4444', value: '500.00' },
  ],
  links: [
    { source: 'renda', target: 'grp-moradia', value: '1500.00' },
    { source: 'grp-moradia', target: 'cat-aluguel', value: '1500.00' },
    { source: 'renda', target: 'deficit', value: '500.00' },
  ],
  collapse_threshold: '50.00',
  income_total: '1000.00',
  deficit: '500.00',
}

describe('buildSankeyGraph', () => {
  it('returns hasData: false for an empty node list', () => {
    const graph = buildSankeyGraph(emptyFlow)
    expect(graph.hasData).toBe(false)
    expect(graph.nodes).toEqual([])
    expect(graph.links).toEqual([])
  })

  it('returns hasData: false when there are nodes but no links', () => {
    const graph = buildSankeyGraph(zeroValueFlow)
    expect(graph.hasData).toBe(false)
    expect(graph.nodes).toHaveLength(1)
  })

  it('resolves string ids into numeric indices and parses money', () => {
    const graph = buildSankeyGraph(simpleFlow)
    expect(graph.hasData).toBe(true)
    expect(graph.nodes).toHaveLength(5)
    expect(graph.nodes[0].numericValue).toBe(5000)

    const rendaIdx = graph.nodes.findIndex((n) => n.id === 'renda')
    const moradiaIdx = graph.nodes.findIndex((n) => n.id === 'grp-moradia')
    const link = graph.links.find((l) => l.sourceId === 'renda' && l.targetId === 'grp-moradia')
    expect(link).toBeDefined()
    expect(link!.source).toBe(rendaIdx)
    expect(link!.target).toBe(moradiaIdx)
    expect(link!.value).toBe(2000)
  })

  it('drops links referencing an id not present in nodes rather than throwing', () => {
    const withDanglingLink: FlowData = {
      ...simpleFlow,
      links: [...simpleFlow.links, { source: 'renda', target: 'ghost', value: '10.00' }],
    }
    const graph = buildSankeyGraph(withDanglingLink)
    expect(graph.links.find((l) => l.targetId === 'ghost')).toBeUndefined()
    expect(graph.links).toHaveLength(simpleFlow.links.length)
  })
})

describe('detectDeficit', () => {
  it('reports no deficit for an empty flow', () => {
    expect(detectDeficit(emptyFlow)).toEqual({ hasDeficit: false, amount: 0, node: null })
  })

  it('reports no deficit when the top-level field is null and no deficit node exists', () => {
    expect(detectDeficit(simpleFlow)).toEqual({ hasDeficit: false, amount: 0, node: null })
  })

  it('reports no deficit when deficit is "0.00"', () => {
    const flow: FlowData = { ...simpleFlow, deficit: '0.00' }
    expect(detectDeficit(flow).hasDeficit).toBe(false)
  })

  it('detects a non-zero deficit and returns its node', () => {
    const info = detectDeficit(deficitFlow)
    expect(info.hasDeficit).toBe(true)
    expect(info.amount).toBe(500)
    expect(info.node?.id).toBe('deficit')
  })

  it('falls back to scanning nodes when the top-level field is absent but a deficit node exists', () => {
    const flow: FlowData = { ...deficitFlow, deficit: null }
    const info = detectDeficit(flow)
    expect(info.hasDeficit).toBe(true)
    expect(info.amount).toBe(500)
  })
})

describe('computeLabelPositions', () => {
  it('returns each ideal position unchanged when nothing collides', () => {
    const positions = computeLabelPositions(
      [
        { index: 0, depth: 0, centerY: 10 },
        { index: 1, depth: 0, centerY: 100 },
      ],
      26,
    )
    expect(positions.get(0)).toBe(10)
    expect(positions.get(1)).toBe(100)
  })

  it('pushes colliding labels apart within the same depth column', () => {
    const positions = computeLabelPositions(
      [
        { index: 0, depth: 1, centerY: 10 },
        { index: 1, depth: 1, centerY: 15 },
        { index: 2, depth: 1, centerY: 20 },
      ],
      26,
    )
    expect(positions.get(0)).toBe(10)
    expect(positions.get(1)).toBe(36)
    expect(positions.get(2)).toBe(62)
  })

  it('keeps separate depth columns independent', () => {
    const positions = computeLabelPositions(
      [
        { index: 0, depth: 0, centerY: 10 },
        { index: 1, depth: 2, centerY: 10 },
      ],
      26,
    )
    expect(positions.get(0)).toBe(10)
    expect(positions.get(1)).toBe(10)
  })

  it('returns an empty map for no inputs', () => {
    expect(computeLabelPositions([], 26).size).toBe(0)
  })
})

describe('formatFlowAmount', () => {
  it('returns the mask verbatim when privacy mode is on', () => {
    expect(formatFlowAmount(1234, 'BRL', 'pt-BR', true, '•••••')).toBe('•••••')
  })

  it('formats a compact currency value when privacy mode is off', () => {
    const result = formatFlowAmount(1234, 'BRL', 'pt-BR', false, '•••••')
    expect(result).not.toBe('•••••')
    expect(result.length).toBeGreaterThan(0)
  })
})

describe('flowSharePct', () => {
  it('computes a percentage of the total to 1 decimal', () => {
    expect(flowSharePct(250, 1000)).toBe('25.0%')
  })

  it('returns "0%" when total is zero', () => {
    expect(flowSharePct(100, 0)).toBe('0%')
  })

  it('returns "0%" when total is negative', () => {
    expect(flowSharePct(100, -50)).toBe('0%')
  })
})

describe('isFlowEmpty', () => {
  it('is true for a flow with no nodes', () => {
    expect(isFlowEmpty(emptyFlow)).toBe(true)
  })

  it('is true when every node value parses to zero', () => {
    expect(isFlowEmpty(zeroValueFlow)).toBe(true)
  })

  it('is false when at least one node has a non-zero value', () => {
    expect(isFlowEmpty(simpleFlow)).toBe(false)
  })
})
