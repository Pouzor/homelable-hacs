import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { ReactFlowProvider } from '@xyflow/react'
import { SideHandles } from '../SideHandles'
import type { NodeData } from '@/types'

function renderHandles(data: Partial<NodeData> = {}) {
  const full: NodeData = { label: 'n', type: 'server', status: 'online', services: [], ...data }
  return render(
    <ReactFlowProvider>
      <SideHandles
        data={full}
        handleBackground="#30363d"
        handleBorder="#30363d"
        labelColor="#8b949e"
      />
    </ReactFlowProvider>
  )
}

describe('SideHandles', () => {
  it('renders a source + invisible target handle per slot', () => {
    // default node: top=1, bottom=1, left=0, right=0
    const { container } = renderHandles({})
    expect(container.querySelectorAll('.react-flow__handle.source').length).toBe(2)
    expect(container.querySelectorAll('.react-flow__handle.target').length).toBe(2)
  })

  it('target (magnet) handle hit area is large enough to snap onto (20px)', () => {
    const { container } = renderHandles({})
    const target = container.querySelector('.react-flow__handle.target') as HTMLElement
    expect(target.style.width).toBe('20px')
    expect(target.style.height).toBe('20px')
    expect(target.style.opacity).toBe('0')
  })

  it('drags start from the source handle, not the target magnet (no direction inversion)', () => {
    // Regression: the target magnet must be drop-only so a drag started on a
    // connection point anchors at the source node (the node you drag FROM),
    // otherwise the edge direction — and thus marker_start/marker_end — inverts.
    const { container } = renderHandles({})
    // React Flow renders isConnectableStart/End as `connectablestart` /
    // `connectableend` classes on the handle element.
    const source = container.querySelector('.react-flow__handle.source') as HTMLElement
    const target = container.querySelector('.react-flow__handle.target') as HTMLElement
    // Source can start a connection but not be a drop end.
    expect(source.classList.contains('connectablestart')).toBe(true)
    expect(source.classList.contains('connectableend')).toBe(false)
    // Target magnet is drop-only: it can be an end but never starts a drag.
    expect(target.classList.contains('connectablestart')).toBe(false)
    expect(target.classList.contains('connectableend')).toBe(true)
  })

  it('renders the source handle on top of (after) the target magnet', () => {
    // DOM order matters: the source must paint over the target so it receives
    // the pointer-down that starts the connection.
    const { container } = renderHandles({})
    const handles = Array.from(container.querySelectorAll('.react-flow__handle'))
    const firstTargetIdx = handles.findIndex((h) => h.classList.contains('target'))
    const firstSourceIdx = handles.findIndex((h) => h.classList.contains('source'))
    expect(firstSourceIdx).toBeGreaterThan(firstTargetIdx)
  })

  it('renders configured per-side counts', () => {
    const { container } = renderHandles({ top_handles: 2, left_handles: 3, right_handles: 1, bottom_handles: 1 })
    expect(container.querySelectorAll('.react-flow__handle.source').length).toBe(7)
  })
})
