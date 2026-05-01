import { CanvasContainer } from './CanvasContainer'
import type { Connection, Edge, Node } from '@xyflow/react'
import type { NodeData, EdgeData } from '@/types'

interface LazyCanvasProps {
  onConnect?: (connection: Connection) => void
  onEdgeDoubleClick?: (edge: Edge<EdgeData>) => void
  onNodeDoubleClick?: (node: Node<NodeData>) => void
  onNodeDragStart?: () => void
  onOpenPending?: (deviceId: string) => void
}

export default function LazyCanvas(props: LazyCanvasProps) {
  return <CanvasContainer {...props} />
}
