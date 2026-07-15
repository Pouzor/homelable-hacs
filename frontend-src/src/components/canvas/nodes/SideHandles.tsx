import type { CSSProperties } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { NodeData } from '@/types'
import {
  SIDES,
  handleId,
  handlePositions,
  isVerticalSide,
  sideHandleCount,
  type Side,
} from '@/utils/handleUtils'

const POSITION: Record<Side, Position> = {
  top: Position.Top,
  bottom: Position.Bottom,
  left: Position.Left,
  right: Position.Right,
}

interface SideHandlesProps {
  data: NodeData
  handleBackground: string
  handleBorder: string
  /** Colour for the optional port-number labels. */
  labelColor: string
  /** Which sides to render. Defaults to all four. */
  sides?: readonly Side[]
  /** When true, render port-number labels if data.show_port_numbers is set. */
  showLabels?: boolean
}

/**
 * Renders the per-side React Flow handles (visible source + invisible target)
 * for a node, spaced along each side's axis. Shared by BaseNode and the
 * container-mode ProxmoxGroupNode so handle IDs stay identical across both.
 */
export function SideHandles({
  data,
  handleBackground,
  handleBorder,
  labelColor,
  sides = SIDES,
  showLabels = false,
}: SideHandlesProps) {
  return (
    <>
      {sides.map((side) => {
        const vertical = isVerticalSide(side)
        return handlePositions(side, sideHandleCount(data, side)).map((pct, idx) => {
          const sourceId = handleId(side, idx)
          const targetId = `${sourceId}-t`
          const offset: CSSProperties = vertical ? { top: `${pct}%` } : { left: `${pct}%` }
          const labelStyle: CSSProperties = vertical
            ? { top: `${pct}%`, [side]: 3, transform: 'translateY(-50%)' }
            : { left: `${pct}%`, [side]: 3, transform: 'translateX(-50%)' }
          // Two overlapping handles per slot:
          //  - target (drop-only): a large invisible magnet, rendered first so
          //    it sits *under* the source. `isConnectableStart={false}` so it
          //    can never *initiate* a drag — otherwise a drag started here would
          //    be target-anchored and invert the edge direction (the node you
          //    drag FROM would become the edge target). See #62 follow-up.
          //  - source (start-only): rendered last so it sits on top and receives
          //    the pointer-down, anchoring the connection at its node. Kept at the
          //    default (small) size so the edge stays pinned to the node border —
          //    RF anchors edges to the handle's *outer* edge, so a large source
          //    would push the endpoint off the border and leave a visible gap.
          //    `isConnectableEnd={false}` so a drop resolves onto the target
          //    magnet beneath (keeping the `-t` targetHandle convention).
          // Net effect: source = the node you drag FROM, target = where you drop.
          return (
            <span key={sourceId}>
              {showLabels && data.show_port_numbers && (
                <span
                  className="absolute font-mono leading-none pointer-events-none select-none"
                  style={{ ...labelStyle, fontSize: 7, color: labelColor }}
                >
                  {idx + 1}
                </span>
              )}
              <Handle
                type="target"
                position={POSITION[side]}
                id={targetId}
                isConnectableStart={false}
                style={{ ...offset, opacity: 0, width: 20, height: 20 }}
              />
              <Handle
                type="source"
                position={POSITION[side]}
                id={sourceId}
                isConnectableEnd={false}
                style={{ ...offset, background: handleBackground, borderColor: handleBorder }}
              />
            </span>
          )
        })
      })}
    </>
  )
}
