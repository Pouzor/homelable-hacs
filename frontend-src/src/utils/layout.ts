import dagre from '@dagrejs/dagre'
import type { Node, Edge } from '@xyflow/react'
import type { NodeData, EdgeData } from '@/types'

const NODE_WIDTH = 180
const NODE_HEIGHT = 52

const PEER_TYPES = new Set(['proxmox', 'switch'])

/**
 * Find groups of peer nodes (same type, directly connected to each other)
 * using union-find. Returns a map: nodeId → groupId (the minimum nodeId in the group).
 */
function buildPeerGroups(
  topLevel: Node<NodeData>[],
  edges: Edge<EdgeData>[],
): Map<string, string> {
  const parent = new Map<string, string>(topLevel.map((n) => [n.id, n.id]))

  function find(id: string): string {
    if (parent.get(id) !== id) parent.set(id, find(parent.get(id)!))
    return parent.get(id)!
  }
  function union(a: string, b: string) {
    const ra = find(a), rb = find(b)
    if (ra !== rb) parent.set(ra, rb)
  }

  const topLevelIds = new Set(topLevel.map((n) => n.id))
  const peerIds = new Set(topLevel.filter((n) => PEER_TYPES.has(n.type ?? '')).map((n) => n.id))

  for (const edge of edges) {
    const { source: s, target: t } = edge
    if (topLevelIds.has(s) && topLevelIds.has(t) && peerIds.has(s) && peerIds.has(t)) {
      // Only merge if both nodes share the same type (proxmox↔proxmox, switch↔switch)
      const srcNode = topLevel.find((n) => n.id === s)!
      const tgtNode = topLevel.find((n) => n.id === t)!
      if (srcNode.type === tgtNode.type) union(s, t)
    }
  }

  // Resolve all to canonical group ids
  const result = new Map<string, string>()
  for (const n of topLevel) result.set(n.id, find(n.id))
  return result
}

/**
 * Apply Dagre hierarchical (top-to-bottom) layout to nodes and edges.
 * Child nodes (parentId set) keep their relative position inside the parent — only
 * top-level nodes are repositioned by Dagre.
 *
 * Post-pass: peer nodes of the same type (proxmox, switch) connected to each other
 * are snapped to the same Y rank so they appear on the same horizontal level.
 */
export function applyDagreLayout(
  nodes: Node<NodeData>[],
  edges: Edge<EdgeData>[],
): Node<NodeData>[] {
  const topLevel = nodes.filter((n) => !n.parentId)
  const topLevelIds = new Set(topLevel.map((n) => n.id))

  // Capture original X positions before Dagre — used to preserve left-to-right
  // ordering of peer groups as the user set them.
  const originalX = new Map<string, number>(topLevel.map((n) => [n.id, n.position.x]))

  // Identify peer groups before running Dagre so we can exclude peer edges
  const peerGroups = buildPeerGroups(topLevel, edges)
  const isPeerEdge = (e: Edge<EdgeData>) => {
    const sg = peerGroups.get(e.source)
    const tg = peerGroups.get(e.target)
    return sg !== undefined && tg !== undefined && sg === tg
  }

  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'TB', nodesep: 60, ranksep: 80 })

  for (const node of topLevel) {
    const w = node.type === 'proxmox' ? (node.width ?? 300) : NODE_WIDTH
    const h = node.type === 'proxmox' ? (node.height ?? 200) : NODE_HEIGHT
    g.setNode(node.id, { width: w, height: h })
  }
  for (const edge of edges) {
    const srcTop = topLevelIds.has(edge.source)
    const tgtTop = topLevelIds.has(edge.target)
    // Exclude peer-to-peer edges — they confuse Dagre's rank assignment
    if (!srcTop || !tgtTop || isPeerEdge(edge)) continue
    // If the edge exits from the TOP handle of the source, the connection goes
    // upward — meaning the source node is visually below the target. Reverse
    // the edge direction for Dagre so it places the source below the target.
    const upward = (edge as { sourceHandle?: string | null }).sourceHandle === 'top'
    if (upward) {
      g.setEdge(edge.target, edge.source)
    } else {
      g.setEdge(edge.source, edge.target)
    }
  }

  dagre.layout(g)

  // Build initial positions from Dagre
  const positions = new Map<string, { x: number; y: number; w: number; h: number }>()
  for (const node of topLevel) {
    const pos = g.node(node.id)
    const w = node.type === 'proxmox' ? (node.width ?? 300) : NODE_WIDTH
    const h = node.type === 'proxmox' ? (node.height ?? 200) : NODE_HEIGHT
    positions.set(node.id, { x: pos.x - w / 2, y: pos.y - h / 2, w, h })
  }

  // Post-pass: fix peer groups (same-type nodes directly connected to each other)
  // Collect members per group
  const groupMembers = new Map<string, string[]>()
  for (const [id, groupId] of peerGroups) {
    if (!groupMembers.has(groupId)) groupMembers.set(groupId, [])
    groupMembers.get(groupId)!.push(id)
  }

  for (const [, members] of groupMembers) {
    if (members.length < 2) continue

    // --- Y: snap all to average Y of the group ---
    const avgY = members.reduce((sum, id) => sum + positions.get(id)!.y, 0) / members.length
    for (const id of members) positions.set(id, { ...positions.get(id)!, y: avgY })

    // --- X: sort by original (pre-layout) X to preserve the user's intended
    //         left-to-right order. Fall back to Dagre X if all nodes share the
    //         same original X (e.g. freshly created canvas with no positions yet). ---
    const GAP = 60
    const origXs = members.map((id) => originalX.get(id) ?? 0)
    const allSameOrigX = origXs.every((x) => x === origXs[0])
    const ordered = members.slice().sort((a, b) =>
      allSameOrigX
        ? positions.get(a)!.x - positions.get(b)!.x   // fall back to Dagre X
        : (originalX.get(a) ?? 0) - (originalX.get(b) ?? 0), // preserve user order
    )
    const totalWidth = ordered.reduce((sum, id) => sum + positions.get(id)!.w, 0) + GAP * (ordered.length - 1)
    const centerX = members.reduce((sum, id) => sum + positions.get(id)!.x + positions.get(id)!.w / 2, 0) / members.length

    let curX = centerX - totalWidth / 2
    for (const id of ordered) {
      const p = positions.get(id)!
      positions.set(id, { ...p, x: curX })
      curX += p.w + GAP
    }
  }

  return nodes.map((node) => {
    if (node.parentId) return node
    const p = positions.get(node.id)!
    return { ...node, position: { x: p.x, y: p.y } }
  })
}
