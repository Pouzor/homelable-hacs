import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { PendingDevicesModal } from '../PendingDevicesModal'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

vi.mock('@/stores/canvasStore', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const useCanvasStore: any = () => ({ addNode: vi.fn(), scanEventTs: 0 })
  useCanvasStore.setState = vi.fn()
  useCanvasStore.getState = () => ({ addNode: vi.fn() })
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
})
