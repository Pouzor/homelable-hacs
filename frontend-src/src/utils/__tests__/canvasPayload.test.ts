import { describe, it, expect } from 'vitest'
import { hydrateCanvasPayload } from '../canvasPayload'

const node = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  type: 'server',
  name: id,
  position_x: 0,
  position_y: 0,
  ...extra,
})

describe('hydrateCanvasPayload', () => {
  it('deserializes nodes and edges', () => {
    const hydrated = hydrateCanvasPayload({
      nodes: [node('a'), node('b')],
      edges: [{ id: 'e1', source: 'a', target: 'b' }],
    })
    expect(hydrated?.nodes.map((n) => n.id)).toEqual(['a', 'b'])
    expect(hydrated?.edges).toHaveLength(1)
    // Domain fields stay on node.data, never hoisted to the node itself.
    expect(hydrated?.nodes[0].data.name).toBe('a')
  })

  it('returns null for a canvas with no nodes, leaving the policy to the caller', () => {
    expect(hydrateCanvasPayload({ nodes: [], edges: [] })).toBeNull()
  })

  it('carries theme, custom style and floor plan out of the viewport blob', () => {
    const floor = { url: '/media/x.png', x: 1, y: 2, width: 3, height: 4, locked: true }
    const hydrated = hydrateCanvasPayload({
      nodes: [node('a')],
      edges: [],
      viewport: { theme_id: 'neon', floor_map: floor },
      custom_style: { nodes: {}, edges: {} },
    })
    expect(hydrated?.themeId).toBe('neon')
    expect(hydrated?.customStyle).toEqual({ nodes: {}, edges: {} })
    expect(hydrated?.floorMap).toEqual(floor)
  })

  it('reports a missing floor plan as null so callers clear the previous one', () => {
    const hydrated = hydrateCanvasPayload({ nodes: [node('a')], edges: [], viewport: {} })
    expect(hydrated?.floorMap).toBeNull()
    expect(hydrated?.themeId).toBeUndefined()
    expect(hydrated?.customStyle).toBeUndefined()
  })

  it('treats a null custom_style as absent', () => {
    const hydrated = hydrateCanvasPayload({ nodes: [node('a')], edges: [], custom_style: null })
    expect(hydrated?.customStyle).toBeUndefined()
  })

  it('marks groups and container-mode nodes as parents', () => {
    const hydrated = hydrateCanvasPayload({
      nodes: [node('g', { type: 'group' }), node('c', { container_mode: true }), node('n')],
      edges: [],
    })
    expect(hydrated?.nodes).toHaveLength(3)
  })
})
