/**
 * Turn a canvas payload from the API into React Flow nodes/edges plus the
 * presentation bits stored alongside them (theme, custom style, floor plan).
 *
 * Shared by the panel (App.tsx) and the Lovelace card, which differ in what
 * they do with an empty canvas: the panel falls back to demo data, the card
 * shows an empty state. Both go through the same deserialization so a canvas
 * looks identical in either.
 */
import type { Edge, Node } from '@xyflow/react'
import {
  deserializeApiNode,
  deserializeApiEdge,
  migrateClusterHandles,
  type ApiNode,
  type ApiEdge,
} from '@/utils/canvasSerializer'
import type { CustomStyleDef, EdgeData, FloorMapConfig, NodeData, ThemeId } from '@/types'

/** The shape `canvasApi.load()` resolves to (`res.data`). */
export interface CanvasPayload {
  nodes: object[]
  edges: object[]
  viewport?: { theme_id?: ThemeId; floor_map?: FloorMapConfig } & Record<string, unknown>
  custom_style?: object | null
}

export interface HydratedCanvas {
  nodes: Node<NodeData>[]
  edges: Edge<EdgeData>[]
  themeId?: ThemeId
  customStyle?: CustomStyleDef
  /** Null when this design has no floor plan — callers must clear the old one. */
  floorMap: FloorMapConfig | null
}

/**
 * Returns null for a canvas with no nodes, leaving the empty-canvas policy to
 * the caller.
 */
export function hydrateCanvasPayload(data: CanvasPayload): HydratedCanvas | null {
  const apiNodes = (data.nodes ?? []) as ApiNode[]
  if (apiNodes.length === 0) return null

  // Groups and container-mode nodes both accept children; the serializer needs
  // to know which ids those are to rewire handles.
  const proxmoxContainerMap = new Map<string, boolean>(
    apiNodes
      .filter((n) => n.type === 'group' || n.container_mode === true)
      .map((n) => [n.id, true])
  )
  const { nodes, edges } = migrateClusterHandles(
    apiNodes.map((n) => deserializeApiNode(n, proxmoxContainerMap)),
    ((data.edges ?? []) as ApiEdge[]).map(deserializeApiEdge)
  )

  return {
    nodes,
    edges,
    themeId: data.viewport?.theme_id,
    customStyle: (data.custom_style as CustomStyleDef | null) ?? undefined,
    floorMap: data.viewport?.floor_map ?? null,
  }
}
