// When a container node toggles container_mode, its domain children
// (nodes whose data.parent_id points at it) need their relationship re-expressed:
//   - container_mode ON  → containment is shown visually (nested), drop the edge
//   - container_mode OFF → node is no longer nested, draw a virtual edge instead
//
// Pure planner: returns which children need a new virtual edge and which existing
// virtual edges should be removed. The caller performs the actual mutations.

export interface VirtualEdgeRef {
  id: string
  source: string
  target: string
}

export interface ContainerEdgeSyncPlan {
  addChildIds: string[]
  removeEdgeIds: string[]
}

export function planContainerModeEdgeSync(
  containerId: string,
  enabled: boolean,
  childIds: string[],
  virtualEdges: VirtualEdgeRef[],
): ContainerEdgeSyncPlan {
  const addChildIds: string[] = []
  const removeEdgeIds: string[] = []

  for (const childId of childIds) {
    const existing = virtualEdges.find(
      (e) =>
        (e.source === childId && e.target === containerId) ||
        (e.source === containerId && e.target === childId),
    )
    if (enabled) {
      if (existing) removeEdgeIds.push(existing.id)
    } else if (!existing) {
      addChildIds.push(childId)
    }
  }

  return { addChildIds, removeEdgeIds }
}
