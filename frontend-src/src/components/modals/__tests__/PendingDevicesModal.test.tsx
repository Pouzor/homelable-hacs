import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { PendingDevicesModal } from '../PendingDevicesModal'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn(), info: vi.fn() } }))

const { mockSetSelectedNode, mockAddNode } = vi.hoisted(() => ({
  mockSetSelectedNode: vi.fn(),
  mockAddNode: vi.fn(),
}))
vi.mock('@/stores/canvasStore', () => {
  const state = { addNode: mockAddNode, scanEventTs: 0, setSelectedNode: mockSetSelectedNode }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const useCanvasStore: any = (sel?: any) => (sel ? sel(state) : state)
  useCanvasStore.setState = vi.fn()
  useCanvasStore.getState = () => state
  return { useCanvasStore }
})
vi.mock('@/stores/designStore', () => ({
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  useDesignStore: (sel: any) => sel({ activeDesignId: 'd1' }),
}))
vi.mock('@/utils/zigbeeProperties', () => ({
  buildZigbeeProperties: () => [],
  isZigbeeType: (t: string) => t.startsWith('zigbee'),
}))
vi.mock('@/utils/zwaveProperties', () => ({
  buildZwaveProperties: () => [],
  isZwaveType: (t: string) => t.startsWith('zwave'),
}))
vi.mock('@/utils/macProperty', () => ({ buildMacProperty: () => [] }))

vi.mock('@/api/ha', () => ({
  scanApi: {
    pending: vi.fn(),
    hidden: vi.fn(),
    restore: vi.fn(),
    bulkRestore: vi.fn(),
    ignore: vi.fn(),
    clearPending: vi.fn(),
    approve: vi.fn(),
    hide: vi.fn(),
    bulkApprove: vi.fn(),
    bulkHide: vi.fn(),
  },
}))

import { scanApi } from '@/api/ha'
import { toast } from 'sonner'
const toastInfo = toast.info

const DEVICE_IP = {
  id: 'dev-a', ip: '10.0.0.5', mac: null, hostname: 'host-a', os: null,
  services: [{ port: 80, protocol: 'tcp', service_name: 'HTTP', category: 'web' }],
  suggested_type: 'server', status: 'pending', discovery_source: 'tcp',
  discovered_at: '2020-01-01T00:00:00Z',
}
const DEVICE_NOSVC = {
  id: 'dev-b', ip: '10.0.0.6', mac: null, hostname: 'host-b', os: null,
  services: [], suggested_type: 'iot', status: 'pending', discovery_source: 'tcp',
  discovered_at: '2020-01-01T00:00:00Z',
}

const DEVICE_ZWAVE = {
  id: 'dev-z', ip: null, mac: null, hostname: 'Z Sensor', os: null,
  ieee_address: 'zwave-0x1-3', vendor: 'Aeotec', model: 'MultiSensor',
  services: [], suggested_type: 'zwave_enddevice', status: 'pending',
  source: 'zwave', discovery_source: 'zwavejs2mqtt',
  discovered_at: '2020-01-01T00:00:00Z',
}

const baseProps = { open: true, onClose: vi.fn() }
const mockPending = vi.mocked(scanApi.pending)
const mockHidden = vi.mocked(scanApi.hidden)
const mockBulkApprove = vi.mocked(scanApi.bulkApprove)
const mockApprove = vi.mocked(scanApi.approve)
const mockToastInfo = vi.mocked(toastInfo)

describe('PendingDevicesModal — Device Inventory', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPending.mockResolvedValue({ data: [DEVICE_IP, DEVICE_NOSVC] } as never)
    mockHidden.mockResolvedValue({ data: [] } as never)
  })

  it('titles the pending view "Device Inventory"', async () => {
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument())
    expect(screen.getByText('Device Inventory')).toBeInTheDocument()
  })

  it('shows "Hidden Devices" title in hidden mode', async () => {
    mockHidden.mockResolvedValue({ data: [{ ...DEVICE_IP, status: 'hidden' }] } as never)
    render(<PendingDevicesModal {...baseProps} initialStatus="hidden" />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument())
    expect(screen.getByText('Hidden Devices')).toBeInTheDocument()
  })

  it('renders a corner canvas-count when canvas_count > 0 (plural)', async () => {
    mockPending.mockResolvedValue({ data: [{ ...DEVICE_IP, canvas_count: 2 }] } as never)
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument())
    expect(screen.getByLabelText('On 2 canvases')).toHaveTextContent('2')
  })

  it('uses singular "canvas" for a single canvas', async () => {
    mockPending.mockResolvedValue({ data: [{ ...DEVICE_IP, canvas_count: 1 }] } as never)
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument())
    expect(screen.getByLabelText('On 1 canvas')).toHaveTextContent('1')
  })

  it('does not render the canvas-count corner when canvas_count is 0', async () => {
    mockPending.mockResolvedValue({ data: [{ ...DEVICE_IP, canvas_count: 0 }] } as never)
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument())
    expect(screen.queryByLabelText(/On \d+ canvas/)).not.toBeInTheDocument()
  })

  it('colours the role badge with the node-type accent, not flat grey', async () => {
    mockPending.mockResolvedValue({ data: [DEVICE_ZWAVE] } as never)
    render(<PendingDevicesModal {...baseProps} />)
    const card = await waitFor(() => screen.getByTestId('pending-card-dev-z'))
    // zwave_enddevice accent from the default theme = #a855f7 (violet), applied
    // to both the text colour and a translucent background.
    const badge = within(card).getByText('zwave_enddevice')
    // #a855f7 → rgb(168, 85, 247) once jsdom normalises the inline colour.
    expect(badge).toHaveStyle({ color: 'rgb(168, 85, 247)' })
    expect(badge.className).not.toContain('text-muted-foreground')
  })

  it('shows a "Discovered" fallback timestamp for a device not on any canvas', async () => {
    mockPending.mockResolvedValue({ data: [{ ...DEVICE_IP, canvas_count: 0 }] } as never)
    render(<PendingDevicesModal {...baseProps} />)
    const card = await waitFor(() => screen.getByTestId('pending-card-dev-a'))
    expect(within(card).getByText('Discovered')).toBeInTheDocument()
    expect(within(card).queryByText('Scan')).not.toBeInTheDocument()
  })

  it('shows linked-node timestamps for a device on a canvas', async () => {
    mockPending.mockResolvedValue({
      data: [{
        ...DEVICE_IP,
        canvas_count: 1,
        node_created_at: '2026-01-02T10:00:00Z',
        node_last_scan: '2026-06-01T08:30:00Z',
        node_last_modified: '2026-06-20T12:00:00Z',
        node_last_seen: '2026-06-25T09:15:00Z',
      }],
    } as never)
    render(<PendingDevicesModal {...baseProps} />)
    const card = await waitFor(() => screen.getByTestId('pending-card-dev-a'))
    expect(within(card).getByText('Created')).toBeInTheDocument()
    expect(within(card).getByText('Scan')).toBeInTheDocument()
    expect(within(card).getByText('Modified')).toBeInTheDocument()
    expect(within(card).getByText('Seen')).toBeInTheDocument()
    // The discovery fallback is not shown once node timestamps exist.
    expect(within(card).queryByText('Discovered')).not.toBeInTheDocument()
  })

  it('filters to devices with detected services when "With services" is on', async () => {
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-b')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /With services/ }))
    expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument()
    expect(screen.queryByTestId('pending-card-dev-b')).not.toBeInTheDocument()
  })

  it('shows on-canvas devices by default and hides them when toggled off', async () => {
    mockPending.mockResolvedValue({
      data: [DEVICE_IP, { ...DEVICE_NOSVC, canvas_count: 1 }],
    } as never)
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-b')).toBeInTheDocument())
    expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Hide on-canvas/ }))
    expect(screen.queryByTestId('pending-card-dev-b')).not.toBeInTheDocument()
    expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument()
  })

  it('filters to Z-Wave devices via the Z-Wave source filter', async () => {
    mockPending.mockResolvedValue({ data: [DEVICE_IP, DEVICE_ZWAVE] } as never)
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-z')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Z-Wave' }))
    expect(screen.getByTestId('pending-card-dev-z')).toBeInTheDocument()
    expect(screen.queryByTestId('pending-card-dev-a')).not.toBeInTheDocument()
  })

  it('labels a Z-Wave device card with the Z-WAVE source chip', async () => {
    mockPending.mockResolvedValue({ data: [DEVICE_ZWAVE] } as never)
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-z')).toBeInTheDocument())
    expect(screen.getByText('Z-WAVE')).toBeInTheDocument()
  })

  it('keeps approved devices listed after bulk approve (reloads, not strips)', async () => {
    // After approve, pending() still returns the rows (now on-canvas w/ badge),
    // so stripping them locally made the inventory go blank until reopen.
    mockPending
      .mockResolvedValueOnce({ data: [DEVICE_IP, DEVICE_NOSVC] } as never)
      .mockResolvedValue({ data: [
        { ...DEVICE_IP, canvas_count: 1 },
        { ...DEVICE_NOSVC, canvas_count: 1 },
      ] } as never)
    mockBulkApprove.mockResolvedValue({ data: {
      approved: 2,
      device_ids: ['dev-a', 'dev-b'],
      node_ids: ['n-a', 'n-b'],
      edges: [],
      edges_created: 0,
    } } as never)
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Select mode' }))
    fireEvent.click(screen.getByTestId('pending-card-dev-a'))
    fireEvent.click(screen.getByTestId('pending-card-dev-b'))
    fireEvent.click(screen.getByRole('button', { name: /Approve \(2\)/ }))
    await waitFor(() => expect(mockBulkApprove).toHaveBeenCalled())
    // Reloaded (initial + post-approve), so rows stay visible with a fresh
    // canvas_count instead of the list going empty.
    await waitFor(() => expect(mockPending).toHaveBeenCalledTimes(2))
    expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument()
    expect(screen.getByTestId('pending-card-dev-b')).toBeInTheDocument()
  })

  it('bulk approve reports devices skipped as already on the canvas', async () => {
    mockBulkApprove.mockResolvedValue({ data: {
      approved: 1,
      device_ids: ['dev-b'],
      node_ids: ['n-b'],
      edges: [],
      edges_created: 0,
      skipped: ['dev-a'],
      skipped_devices: [
        { device_id: 'dev-a', label: 'host-a', match: 'ip', value: '10.0.0.5', existing_node_id: 'n-a' },
      ],
    } } as never)
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Select mode' }))
    fireEvent.click(screen.getByTestId('pending-card-dev-a'))
    fireEvent.click(screen.getByTestId('pending-card-dev-b'))
    fireEvent.click(screen.getByRole('button', { name: /Approve \(2\)/ }))
    await waitFor(() =>
      expect(mockToastInfo).toHaveBeenCalledWith(
        expect.stringContaining('1 already on this canvas, skipped: host-a'),
        expect.anything(),
      ),
    )
  })
})

describe('PendingDevicesModal — duplicate approve prompt', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPending.mockResolvedValue({ data: [DEVICE_IP] } as never)
    mockHidden.mockResolvedValue({ data: [] } as never)
  })

  const openDetailAndApprove = async () => {
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('pending-card-dev-a')) // open PendingDeviceModal
    fireEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
  }

  const CONFLICT = {
    duplicate: true as const,
    existing_node_id: 'n-existing',
    existing_label: 'Existing Host',
    match: 'ip' as const,
    value: '10.0.0.5',
  }

  it('prompts instead of duplicating when the host is already on this design', async () => {
    mockApprove.mockResolvedValue({ data: { duplicate: CONFLICT } } as never)
    await openDetailAndApprove()
    await waitFor(() =>
      expect(screen.getByText('Device already on this canvas')).toBeInTheDocument(),
    )
    expect(screen.getByText('Existing Host')).toBeInTheDocument()
  })

  it('"Add duplicate anyway" retries the approve with force=true', async () => {
    mockApprove
      .mockResolvedValueOnce({ data: { duplicate: CONFLICT } } as never)
      .mockResolvedValueOnce({ data: { node_id: 'n-new', edges: [], edges_created: 0 } } as never)
    await openDetailAndApprove()
    fireEvent.click(await screen.findByRole('button', { name: /Add duplicate anyway/ }))
    await waitFor(() =>
      expect(mockApprove).toHaveBeenLastCalledWith(
        'dev-a',
        expect.objectContaining({ force: true }),
        'd1',
      ),
    )
  })

  it('"Go to existing node" selects it and closes', async () => {
    const onClose = vi.fn()
    mockApprove.mockResolvedValue({ data: { duplicate: CONFLICT } } as never)
    render(<PendingDevicesModal open onClose={onClose} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-a')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('pending-card-dev-a'))
    fireEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Go to existing node/ }))
    expect(mockSetSelectedNode).toHaveBeenCalledWith('n-existing')
    expect(onClose).toHaveBeenCalled()
  })
})

describe('PendingDevicesModal — preserves backend node properties (Proxmox)', () => {
  // A Proxmox guest carries hidden spec rows on the pending device; approve must
  // put them on the canvas node, not rebuild from scratch (which dropped them).
  const PROXMOX_DEVICE = {
    id: 'dev-px', ip: '192.168.1.112', mac: 'bc:24:11:4f:12:81', hostname: 'glpi',
    os: null, services: [], suggested_type: 'server', status: 'pending',
    discovery_source: 'proxmox', discovery_sources: ['proxmox'], source: 'proxmox',
    discovered_at: '2020-01-01T00:00:00Z',
    properties: [
      { key: 'VMID', value: '102', icon: null, visible: false },
      { key: 'Kind', value: 'LXC', icon: null, visible: false },
      { key: 'Source', value: 'Proxmox VE', icon: null, visible: false },
    ],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockPending.mockResolvedValue({ data: [PROXMOX_DEVICE] } as never)
    mockHidden.mockResolvedValue({ data: [] } as never)
  })

  it('single approve carries the Proxmox spec rows onto the node', async () => {
    mockApprove.mockResolvedValue({ data: { node_id: 'n-px', edges: [], edges_created: 0 } } as never)
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-px')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('pending-card-dev-px'))
    fireEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await waitFor(() => expect(mockAddNode).toHaveBeenCalled())
    const keys = (mockAddNode.mock.calls[0][0].data.properties as { key: string }[]).map((p) => p.key)
    expect(keys).toEqual(expect.arrayContaining(['VMID', 'Kind', 'Source']))
  })

  it('bulk approve carries the Proxmox spec rows onto each node', async () => {
    mockBulkApprove.mockResolvedValue({ data: {
      approved: 1, device_ids: ['dev-px'], node_ids: ['n-px'],
      edges: [], edges_created: 0, skipped: [], skipped_devices: [],
    } } as never)
    render(<PendingDevicesModal {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('pending-card-dev-px')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Select mode' }))
    fireEvent.click(screen.getByTestId('pending-card-dev-px'))
    fireEvent.click(screen.getByRole('button', { name: /Approve \(1\)/ }))
    await waitFor(() => expect(mockAddNode).toHaveBeenCalled())
    const keys = (mockAddNode.mock.calls[0][0].data.properties as { key: string }[]).map((p) => p.key)
    expect(keys).toEqual(expect.arrayContaining(['VMID', 'Kind', 'Source']))
  })
})
