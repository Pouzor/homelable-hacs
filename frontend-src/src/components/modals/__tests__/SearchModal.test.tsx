import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { SearchModal } from '../SearchModal'
import { useCanvasStore } from '@/stores/canvasStore'
import type { Node } from '@xyflow/react'
import type { NodeData } from '@/types'

const mockFitView = vi.fn()
vi.mock('@xyflow/react', () => ({
  useReactFlow: () => ({ fitView: mockFitView }),
}))

const mockDocSearch = vi.fn()
const mockPending = vi.fn().mockResolvedValue({ data: [] })
vi.mock('@/api/client', () => ({
  scanApi: { pending: (...args: unknown[]) => mockPending(...args) },
  documentsApi: { search: (...args: unknown[]) => mockDocSearch(...args) },
}))

const mockOnOpenPending = vi.fn()

function makeNode(id: string, overrides: Partial<NodeData> = {}): Node<NodeData> {
  return {
    id,
    type: overrides.type ?? 'server',
    position: { x: 0, y: 0 },
    data: {
      label: id,
      type: overrides.type ?? 'server',
      status: 'unknown',
      services: [],
      ...overrides,
    },
  }
}

describe('SearchModal', () => {
  beforeEach(() => {
    useCanvasStore.setState({ nodes: [], edges: [], selectedNodeId: null })
    mockFitView.mockReset()
  })

  it('renders nothing when closed', () => {
    render(<SearchModal open={false} onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    expect(screen.queryByPlaceholderText(/search nodes/i)).toBeNull()
  })

  it('renders search input when open', () => {
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    expect(screen.getByPlaceholderText(/search nodes/i)).toBeDefined()
  })

  it('shows "Type to search" hint when query is empty', () => {
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    expect(screen.getByText(/type to search/i)).toBeDefined()
  })

  it('shows no results message when query has no matches', () => {
    useCanvasStore.setState({ nodes: [makeNode('router', { label: 'Router' })] })
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: 'zzz' } })
    expect(screen.getByText(/no results match/i)).toBeDefined()
  })

  it('filters nodes by label', () => {
    useCanvasStore.setState({
      nodes: [makeNode('n1', { label: 'My Router' }), makeNode('n2', { label: 'NAS Server' })],
    })
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: 'router' } })
    expect(screen.getByText('My Router')).toBeDefined()
    expect(screen.queryByText('NAS Server')).toBeNull()
  })

  it('filters nodes by IP', () => {
    useCanvasStore.setState({
      nodes: [
        makeNode('n1', { label: 'Box A', ip: '192.168.1.10' }),
        makeNode('n2', { label: 'Box B', ip: '10.0.0.1' }),
      ],
    })
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: '192.168' } })
    expect(screen.getByText('Box A')).toBeDefined()
    expect(screen.queryByText('Box B')).toBeNull()
  })

  it('filters nodes by hostname', () => {
    useCanvasStore.setState({
      nodes: [
        makeNode('n1', { label: 'A', hostname: 'pve.local' }),
        makeNode('n2', { label: 'B', hostname: 'nas.local' }),
      ],
    })
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: 'pve' } })
    expect(screen.getByText('A')).toBeDefined()
    expect(screen.queryByText('B')).toBeNull()
  })

  it('excludes groupRect nodes from results', () => {
    useCanvasStore.setState({
      nodes: [
        makeNode('n1', { label: 'Server', type: 'server' }),
        makeNode('g1', { label: 'Zone A', type: 'groupRect' }),
      ],
    })
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: 'zone' } })
    expect(screen.getByText(/no results match/i)).toBeDefined()
  })

  it('limits node results to 6', () => {
    useCanvasStore.setState({
      nodes: Array.from({ length: 12 }, (_, i) => makeNode(`n${i}`, { label: `Server ${i}` })),
    })
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: 'server' } })
    const items = screen.getAllByText(/Server \d/)
    expect(items).toHaveLength(6)
  })

  it('selects node and closes on result click', () => {
    const onClose = vi.fn()
    useCanvasStore.setState({ nodes: [makeNode('n1', { label: 'Proxmox' })] })
    render(<SearchModal open onClose={onClose} onOpenPending={mockOnOpenPending} />)
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: 'prox' } })
    fireEvent.click(screen.getByText('Proxmox'))
    expect(useCanvasStore.getState().selectedNodeId).toBe('n1')
    expect(mockFitView).toHaveBeenCalledWith(expect.objectContaining({ nodes: [{ id: 'n1' }] }))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('selects first result and closes on Enter key', () => {
    const onClose = vi.fn()
    useCanvasStore.setState({ nodes: [makeNode('n1', { label: 'Switch' })] })
    render(<SearchModal open onClose={onClose} onOpenPending={mockOnOpenPending} />)
    const input = screen.getByPlaceholderText(/search nodes/i)
    fireEvent.change(input, { target: { value: 'switch' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(useCanvasStore.getState().selectedNodeId).toBe('n1')
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('closes on Escape key', () => {
    const onClose = vi.fn()
    render(<SearchModal open onClose={onClose} onOpenPending={mockOnOpenPending} />)
    fireEvent.keyDown(screen.getByPlaceholderText(/search nodes/i), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('closes when clicking backdrop', () => {
    const onClose = vi.fn()
    render(<SearchModal open onClose={onClose} onOpenPending={mockOnOpenPending} />)
    // The backdrop is the fixed inset div — clicking it fires onClose
    const backdrop = document.querySelector('.fixed.inset-0') as HTMLElement
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('does not close when clicking inside the search box', () => {
    const onClose = vi.fn()
    render(<SearchModal open onClose={onClose} onOpenPending={mockOnOpenPending} />)
    fireEvent.click(screen.getByPlaceholderText(/search nodes/i))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('search is case-insensitive', () => {
    useCanvasStore.setState({ nodes: [makeNode('n1', { label: 'My NAS' })] })
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: 'MY NAS' } })
    expect(screen.getByText('My NAS')).toBeDefined()
  })
})

describe('SearchModal — documents (homelable #421)', () => {
  beforeEach(() => {
    useCanvasStore.setState({ nodes: [], edges: [], selectedNodeId: null })
    mockDocSearch.mockReset()
    mockDocSearch.mockResolvedValue({
      data: {
        engine: 'like',
        hits: [{ doc_id: 'd1', title: 'Backups', kind: 'page', snippet: 'Restic runs nightly', device_id: null }],
      },
    })
  })

  it('lists document hits and opens the one picked', async () => {
    const onOpenDocument = vi.fn()
    const onClose = vi.fn()
    render(<SearchModal open onClose={onClose} onOpenPending={mockOnOpenPending} onOpenDocument={onOpenDocument} />)
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: 'restic' } })

    const hit = await screen.findByText('Backups')
    expect(mockDocSearch).toHaveBeenCalledWith('restic', 5)
    expect(screen.getByText('Restic runs nightly')).toBeDefined()
    fireEvent.click(hit)
    expect(onOpenDocument).toHaveBeenCalledWith('d1')
    expect(onClose).toHaveBeenCalled()
  })

  it('does not ask for documents where none can be opened', async () => {
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: 'restic' } })
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(mockDocSearch).not.toHaveBeenCalled()
  })

  it('waits for two characters before searching documents', async () => {
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} onOpenDocument={vi.fn()} />)
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: 'r' } })
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(mockDocSearch).not.toHaveBeenCalled()
  })
})

describe('SearchModal — devices with no IP', () => {
  it('searches past a Zigbee device that has no address', async () => {
    useCanvasStore.setState({ nodes: [], edges: [], selectedNodeId: null })
    mockPending.mockResolvedValueOnce({
      data: [
        { id: 'z1', ip: null, hostname: 'kitchen-plug', services: [], status: 'pending' },
        { id: 'd2', ip: '192.168.1.9', hostname: 'nas', services: [], status: 'pending' },
      ],
    })
    render(<SearchModal open onClose={vi.fn()} onOpenPending={mockOnOpenPending} />)
    await waitFor(() => expect(mockPending).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    fireEvent.change(screen.getByPlaceholderText(/search nodes/i), { target: { value: 'kitchen' } })
    expect(screen.getByText('kitchen-plug')).toBeDefined()
  })
})
