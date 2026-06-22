import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { Sidebar } from '../Sidebar'
import { useCanvasStore } from '@/stores/canvasStore'
import type { Node } from '@xyflow/react'
import type { NodeData } from '@/types'

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('@/stores/canvasStore')

const mockBulkApprove = vi.fn()
const mockBulkHide = vi.fn()

vi.mock('@/api/client', () => ({
  scanApi: {
    trigger: vi.fn().mockResolvedValue({}),
    pending: vi.fn().mockResolvedValue({ data: [] }),
    hidden: vi.fn().mockResolvedValue({ data: [] }),
    runs: vi.fn().mockResolvedValue({ data: [] }),
    stop: vi.fn().mockResolvedValue({}),
    clearPending: vi.fn().mockResolvedValue({}),
    approve: vi.fn().mockResolvedValue({ data: { approved: true, node_id: 'new-node-1' } }),
    hide: vi.fn().mockResolvedValue({ data: { hidden: true } }),
    ignore: vi.fn().mockResolvedValue({ data: { ignored: true } }),
    bulkApprove: (...args: unknown[]) => mockBulkApprove(...args),
    bulkHide: (...args: unknown[]) => mockBulkHide(...args),
  },
  subscribeScan: vi.fn().mockResolvedValue(() => {}),
}))

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('@/components/ui/Logo', () => ({
  Logo: ({ showText }: { showText: boolean }) => (
    <div data-testid="logo" data-show-text={showText} />
  ),
}))

vi.mock('@/components/ui/tooltip', () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: () => null,
}))

vi.mock('@/components/modals/PendingDeviceModal', () => ({
  PendingDeviceModal: () => null,
}))

// Replace heavy modal subtrees with stubs that just announce open/closed state.
vi.mock('@/components/modals/PendingDevicesModal', () => ({
  PendingDevicesModal: ({ open, initialStatus }: { open: boolean; initialStatus?: string }) =>
    open ? <div data-testid="pending-devices-modal" data-initial-status={initialStatus ?? 'pending'} /> : null,
}))

vi.mock('@/components/zigbee/ZigbeeImportModal', () => ({
  ZigbeeImportModal: ({ open }: { open: boolean }) =>
    open ? <div data-testid="zigbee-import-modal" /> : null,
}))

// ── Helpers ───────────────────────────────────────────────────────────────────

const makeNode = (id: string, status: NodeData['status'], type: NodeData['type'] = 'server'): Node<NodeData> => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data: { label: id, type, status, services: [] },
})

const mockToggleHideIp = vi.fn()

function mockStore(overrides: Partial<ReturnType<typeof useCanvasStore>> = {}) {
  vi.mocked(useCanvasStore).mockReturnValue({
    nodes: [],
    hasUnsavedChanges: false,
    hideIp: false,
    toggleHideIp: mockToggleHideIp,
    addNode: vi.fn(),
    scanEventTs: 0,
    ...overrides,
  } as ReturnType<typeof useCanvasStore>)
}

const defaultProps = {
  onAddNode: vi.fn(),
  onAddGroupRect: vi.fn(),
  onScan: vi.fn(),
  onSave: vi.fn(),
  onNodeApproved: vi.fn(),
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('Sidebar', () => {
  beforeEach(() => {
    mockStore()
    vi.clearAllMocks()
  })

  // ── Render ─────────────────────────────────────────────────────────────────

  it('renders logo and nav items', () => {
    render(<Sidebar {...defaultProps} />)
    expect(screen.getByTestId('logo')).toBeInTheDocument()
    expect(screen.getByText('Add Node')).toBeInTheDocument()
    expect(screen.getByText('Save Canvas')).toBeInTheDocument()
    expect(screen.getByText('Scan Network')).toBeInTheDocument()
  })

  it('calls onSave with no arguments when Save Canvas is clicked', () => {
    // Regression: passing the click event leaks it as handleSave's
    // designIdOverride, so the save lands under the wrong design id.
    const onSave = vi.fn()
    render(<Sidebar {...defaultProps} onSave={onSave} />)
    fireEvent.click(screen.getByText('Save Canvas'))
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith()
  })

  it('shows all view nav items', () => {
    render(<Sidebar {...defaultProps} />)
    expect(screen.getByText('Canvas')).toBeInTheDocument()
    expect(screen.getByText('Pending Devices')).toBeInTheDocument()
    expect(screen.getByText('Hidden Devices')).toBeInTheDocument()
    expect(screen.getByText('Scan History')).toBeInTheDocument()
  })

  // ── Stats ──────────────────────────────────────────────────────────────────

  it('displays total / online / offline counts from store', () => {
    mockStore({
      nodes: [
        makeNode('n1', 'online'),
        makeNode('n2', 'online'),
        makeNode('n3', 'offline'),
        makeNode('n4', 'unknown'),
      ],
    })
    render(<Sidebar {...defaultProps} />)
    // Total (excludes groupRect)
    expect(screen.getByText('4')).toBeInTheDocument()
    // Online
    expect(screen.getByText('2')).toBeInTheDocument()
    // Offline
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it('excludes groupRect nodes from stats', () => {
    mockStore({
      nodes: [
        makeNode('n1', 'unknown'),  // 1 real node, not online/offline
        makeNode('zone', 'unknown', 'groupRect'),
      ],
    })
    render(<Sidebar {...defaultProps} />)
    // Total row shows 1 (groupRect excluded), online/offline both 0
    const totalRow = screen.getByText('Total').closest('div')!
    expect(totalRow).toHaveTextContent('1')
    expect(screen.getAllByText('0')).toHaveLength(2) // online=0, offline=0
  })

  // ── Collapse ───────────────────────────────────────────────────────────────

  it('collapses sidebar on toggle button click', () => {
    render(<Sidebar {...defaultProps} />)
    const aside = screen.getByRole('complementary')
    expect(aside).toHaveStyle({ width: '220px' })

    const toggle = aside.querySelector('button')!
    fireEvent.click(toggle)
    expect(aside).toHaveStyle({ width: '48px' })
  })

  it('hides label text when collapsed', () => {
    render(<Sidebar {...defaultProps} />)
    const aside = screen.getByRole('complementary')
    const toggle = aside.querySelector('button')!
    fireEvent.click(toggle)
    expect(screen.queryByText('Add Node')).not.toBeInTheDocument()
  })

  it('hides stats footer when collapsed', () => {
    render(<Sidebar {...defaultProps} />)
    expect(screen.getByText('Total')).toBeInTheDocument()
    const toggle = screen.getByRole('complementary').querySelector('button')!
    fireEvent.click(toggle)
    expect(screen.queryByText('Total')).not.toBeInTheDocument()
  })

  it('shows logo with showText=false when collapsed', () => {
    render(<Sidebar {...defaultProps} />)
    const logo = screen.getByTestId('logo')
    expect(logo).toHaveAttribute('data-show-text', 'true')
    const toggle = screen.getByRole('complementary').querySelector('button')!
    fireEvent.click(toggle)
    expect(logo).toHaveAttribute('data-show-text', 'false')
  })

  // ── Action callbacks ───────────────────────────────────────────────────────

  it('calls onAddNode when Add Node is clicked', () => {
    render(<Sidebar {...defaultProps} />)
    fireEvent.click(screen.getByText('Add Node'))
    expect(defaultProps.onAddNode).toHaveBeenCalledOnce()
  })

  it('calls onAddGroupRect when Add Zone is clicked', () => {
    render(<Sidebar {...defaultProps} />)
    fireEvent.click(screen.getByText('Add Zone'))
    expect(defaultProps.onAddGroupRect).toHaveBeenCalledOnce()
  })

  it('calls onSave when Save Canvas is clicked', () => {
    render(<Sidebar {...defaultProps} />)
    fireEvent.click(screen.getByText('Save Canvas'))
    expect(defaultProps.onSave).toHaveBeenCalledOnce()
  })

  it('calls toggleHideIp when Hide IPs is clicked', () => {
    render(<Sidebar {...defaultProps} />)
    fireEvent.click(screen.getByText('Hide IPs'))
    expect(mockToggleHideIp).toHaveBeenCalledOnce()
  })

  it('shows Show IPs label when hideIp is true', () => {
    mockStore({ hideIp: true })
    render(<Sidebar {...defaultProps} />)
    expect(screen.getByText('Show IPs')).toBeInTheDocument()
  })

  // ── Unsaved changes badge ──────────────────────────────────────────────────

  it('shows unsaved badge dot on Save Canvas when hasUnsavedChanges', () => {
    mockStore({ hasUnsavedChanges: true })
    render(<Sidebar {...defaultProps} />)
    // The badge is a span sibling of the Save Canvas button icon
    const saveBtn = screen.getByText('Save Canvas').closest('button')!
    const badge = saveBtn.querySelector('span.rounded-full')
    expect(badge).toBeInTheDocument()
  })

  it('does not show unsaved badge when no changes', () => {
    mockStore({ hasUnsavedChanges: false })
    render(<Sidebar {...defaultProps} />)
    const saveBtn = screen.getByText('Save Canvas').closest('button')!
    const badge = saveBtn.querySelector('span.rounded-full')
    expect(badge).not.toBeInTheDocument()
  })

  // ── Scan action ────────────────────────────────────────────────────────────

  it('calls onScan prop when Scan Network is clicked (scan trigger moved to ScanConfigModal)', () => {
    render(<Sidebar {...defaultProps} />)
    fireEvent.click(screen.getByText('Scan Network'))
    expect(defaultProps.onScan).toHaveBeenCalledOnce()
  })

  // ── Navigation ─────────────────────────────────────────────────────────────

  it('opens PendingDevicesModal with status=pending when Pending Devices nav item is clicked', async () => {
    render(<Sidebar {...defaultProps} />)
    fireEvent.click(screen.getByText('Pending Devices'))
    await waitFor(() => {
      const modal = screen.getByTestId('pending-devices-modal')
      expect(modal).toBeInTheDocument()
      expect(modal).toHaveAttribute('data-initial-status', 'pending')
    })
  })

  it('opens PendingDevicesModal with status=hidden when Hidden Devices nav item is clicked', async () => {
    render(<Sidebar {...defaultProps} />)
    fireEvent.click(screen.getByText('Hidden Devices'))
    await waitFor(() => {
      const modal = screen.getByTestId('pending-devices-modal')
      expect(modal).toBeInTheDocument()
      expect(modal).toHaveAttribute('data-initial-status', 'hidden')
    })
  })

  it('shows History panel when Scan History nav item is clicked', async () => {
    render(<Sidebar {...defaultProps} />)
    fireEvent.click(screen.getByText('Scan History'))
    await waitFor(() => expect(screen.getByText('No scans yet')).toBeInTheDocument())
  })

  it('opens ZigbeeImportModal when Import Zigbee is clicked', async () => {
    render(<Sidebar {...defaultProps} />)
    fireEvent.click(screen.getByText('Import Zigbee'))
    await waitFor(() => expect(screen.getByTestId('zigbee-import-modal')).toBeInTheDocument())
  })

})

// PendingDevicesPanel inline view + its live-scan-stream subscription were
// removed in favour of PendingDevicesModal. Coverage for batch select +
// live updates belongs in tests targeting PendingDevicesModal directly
// (tracked as a follow-up, see PR description).
