import { describe, it, expect } from 'vitest'
import { planContainerModeEdgeSync, type VirtualEdgeRef } from '../containerEdgeSync'

describe('planContainerModeEdgeSync', () => {
  it('adds an edge for each child with no existing virtual edge when turning OFF', () => {
    const plan = planContainerModeEdgeSync('px1', false, ['c1', 'c2'], [])
    expect(plan).toEqual({ addChildIds: ['c1', 'c2'], removeEdgeIds: [] })
  })

  it('does not duplicate an edge that already exists when turning OFF', () => {
    const edges: VirtualEdgeRef[] = [{ id: 'e1', source: 'c1', target: 'px1' }]
    const plan = planContainerModeEdgeSync('px1', false, ['c1', 'c2'], edges)
    expect(plan).toEqual({ addChildIds: ['c2'], removeEdgeIds: [] })
  })

  it('matches an existing edge regardless of source/target direction', () => {
    const edges: VirtualEdgeRef[] = [{ id: 'e1', source: 'px1', target: 'c1' }]
    const plan = planContainerModeEdgeSync('px1', false, ['c1'], edges)
    expect(plan).toEqual({ addChildIds: [], removeEdgeIds: [] })
  })

  it('removes existing virtual edges for children when turning ON', () => {
    const edges: VirtualEdgeRef[] = [
      { id: 'e1', source: 'c1', target: 'px1' },
      { id: 'e2', source: 'px1', target: 'c2' },
    ]
    const plan = planContainerModeEdgeSync('px1', true, ['c1', 'c2'], edges)
    expect(plan).toEqual({ addChildIds: [], removeEdgeIds: ['e1', 'e2'] })
  })

  it('removes nothing when turning ON and no edges exist', () => {
    const plan = planContainerModeEdgeSync('px1', true, ['c1'], [])
    expect(plan).toEqual({ addChildIds: [], removeEdgeIds: [] })
  })

  it('ignores virtual edges unrelated to the container or its children', () => {
    const edges: VirtualEdgeRef[] = [{ id: 'e9', source: 'a', target: 'b' }]
    const plan = planContainerModeEdgeSync('px1', false, ['c1'], edges)
    expect(plan).toEqual({ addChildIds: ['c1'], removeEdgeIds: [] })
  })

  it('returns empty plan when the container has no children', () => {
    const plan = planContainerModeEdgeSync('px1', false, [], [])
    expect(plan).toEqual({ addChildIds: [], removeEdgeIds: [] })
  })
})
