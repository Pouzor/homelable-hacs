import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { DetailPanel } from '../DetailPanel'
import * as canvasStore from '@/stores/canvasStore'
import type { NodeData } from '@/types'
import type { Node } from '@xyflow/react'

vi.mock('@/stores/canvasStore')
vi.mock('@/api/client', () => ({ checkNodeStatus: vi.fn() }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { checkNodeStatus } from '@/api/client'
import { toast } from 'sonner'

function setupStore(data: Partial<NodeData> = {}) {
  const node: Node<NodeData> = {
    id: 'n1',
    type: data.type ?? 'server',
    position: { x: 0, y: 0 },
    data: { label: 'Test Node', type: 'server', status: 'offline', services: [], ...data },
  }
  vi.mocked(canvasStore.useCanvasStore).mockReturnValue({
    nodes: [node],
    selectedNodeId: 'n1',
    selectedNodeIds: [],
    setSelectedNode: vi.fn(),
    deleteNode: vi.fn(),
    updateNode: vi.fn(),
    snapshotHistory: vi.fn(),
    createGroup: vi.fn(),
    ungroup: vi.fn(),
    setNodeSize: vi.fn(),
  } as unknown as ReturnType<typeof canvasStore.useCanvasStore>)
}

describe('DetailPanel — Check now', () => {
  beforeEach(() => {
    vi.mocked(checkNodeStatus).mockReset()
    vi.mocked(toast.success).mockReset()
    vi.mocked(toast.error).mockReset()
  })

  it('runs the check for the selected node and reports the result', async () => {
    vi.mocked(checkNodeStatus).mockResolvedValue({ status: 'online', response_time_ms: 9 })
    setupStore({ check_method: 'http' })
    render(<DetailPanel onEdit={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Check now' }))

    expect(checkNodeStatus).toHaveBeenCalledWith('n1')
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('http: online (9ms)'))
  })

  it('disables the button while the check is running', async () => {
    let resolve!: (v: { status: string; response_time_ms: number | null }) => void
    vi.mocked(checkNodeStatus).mockReturnValue(new Promise((r) => { resolve = r }))
    setupStore({ check_method: 'ping' })
    render(<DetailPanel onEdit={vi.fn()} />)

    const button = screen.getByRole('button', { name: 'Check now' }) as HTMLButtonElement
    fireEvent.click(button)
    expect(button.disabled).toBe(true)

    resolve({ status: 'offline', response_time_ms: null })
    await waitFor(() => expect(button.disabled).toBe(false))
    expect(toast.success).toHaveBeenCalledWith('ping: offline')
  })

  it('defaults the label to ping when no check method is set', () => {
    setupStore({})
    render(<DetailPanel onEdit={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Check now' }).getAttribute('title')).toContain('(ping)')
  })

  it('asks to save first when the backend does not know the node', async () => {
    vi.mocked(checkNodeStatus).mockRejectedValue({ code: 'not_found', message: 'Node not found' })
    setupStore({ check_method: 'ping' })
    render(<DetailPanel onEdit={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Check now' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('Save the canvas first')))
  })

  it('surfaces other errors', async () => {
    vi.mocked(checkNodeStatus).mockRejectedValue({ code: 'unknown_error', message: 'boom' })
    setupStore({ check_method: 'ping' })
    render(<DetailPanel onEdit={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Check now' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Check failed: boom'))
  })

  it.each([
    [{ check_method: 'none' as const }],
    [{ type: 'zigbee_enddevice' as const }],
    [{ type: 'zwave_enddevice' as const }],
  ])('hides the button for nodes without a live check (%o)', (data) => {
    setupStore(data as Partial<NodeData>)
    render(<DetailPanel onEdit={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Check now' })).toBeNull()
  })
})
