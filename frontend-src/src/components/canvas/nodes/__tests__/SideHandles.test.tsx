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

  it('renders configured per-side counts', () => {
    const { container } = renderHandles({ top_handles: 2, left_handles: 3, right_handles: 1, bottom_handles: 1 })
    expect(container.querySelectorAll('.react-flow__handle.source').length).toBe(7)
  })
})
