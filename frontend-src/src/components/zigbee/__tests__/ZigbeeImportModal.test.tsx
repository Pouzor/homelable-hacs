import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ZigbeeImportModal } from '../ZigbeeImportModal'
import type { ZigbeeBackend, ZigbeeGateway, ZigbeeSource } from '../types'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/api/ha', () => ({
  zigbeeApi: {
    gateway: vi.fn(),
    startImport: vi.fn(),
  },
}))

import { zigbeeApi } from '@/api/ha'
import { toast } from 'sonner'

function mockGateway(source: ZigbeeSource, resolved: ZigbeeBackend, zhaDetected = true) {
  const data: ZigbeeGateway = { source, resolved, zha_detected: zhaDetected }
  vi.mocked(zigbeeApi.gateway).mockResolvedValue({ data } as never)
}

describe('ZigbeeImportModal', () => {
  beforeEach(() => {
    vi.mocked(toast.success).mockReset()
    vi.mocked(toast.error).mockReset()
    vi.mocked(zigbeeApi.gateway).mockReset()
    vi.mocked(zigbeeApi.startImport).mockReset()
    vi.mocked(zigbeeApi.startImport).mockResolvedValue({
      data: { run_id: 'r1', status: 'running', devices_found: 0, backend: 'zha' },
    } as never)
  })

  it('renders the import dialog when open', async () => {
    mockGateway('z2m', 'z2m', false)
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    expect(screen.getByText('Zigbee Import')).toBeDefined()
    expect(screen.getByRole('button', { name: /Start Zigbee scan/i })).toBeDefined()
    await waitFor(() => expect(zigbeeApi.gateway).toHaveBeenCalled())
  })

  it('names both gateways whichever one is in use', async () => {
    mockGateway('zha', 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(zigbeeApi.gateway).toHaveBeenCalled())
    expect(screen.getAllByText('ZHA').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Zigbee2MQTT').length).toBeGreaterThan(0)
  })

  it('names the configured gateway and offers no picker', async () => {
    mockGateway('zha', 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/Gateway:/i)).toBeDefined())
    // The gateway is a setting, not a per-import choice.
    expect(screen.queryByRole('button', { name: 'Zigbee2MQTT' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'ZHA' })).toBeNull()
    // ZHA-specific copy: no broker, near-instant.
    expect(screen.getByText(/no MQTT broker/i)).toBeDefined()
  })

  it('marks an auto-resolved gateway as auto-detected', async () => {
    mockGateway('auto', 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/auto-detected/i)).toBeDefined())
  })

  it('does not call a gateway auto-detected when it was configured explicitly', async () => {
    mockGateway('zha', 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/Gateway:/i)).toBeDefined())
    expect(screen.queryByText(/auto-detected/i)).toBeNull()
  })

  it('points at the integration options to switch gateway', async () => {
    mockGateway('zha', 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/Running the other one/i)).toBeDefined())
    expect(screen.getByText('Zigbee gateway')).toBeDefined()
  })

  it('never shows Z2M-only copy to a ZHA user', async () => {
    mockGateway('zha', 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/Gateway:/i)).toBeDefined())
    expect(screen.queryByText(/base topic/i)).toBeNull()
  })

  it('claims no gateway until the probe answers', () => {
    mockGateway('zha', 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    // Rendered before the probe resolves: no backend asserted either way.
    expect(screen.getByText(/checking/i)).toBeDefined()
    expect(screen.queryByText(/base topic/i)).toBeNull()
    expect(screen.queryByText(/no MQTT broker/i)).toBeNull()
  })

  it('falls back to neutral copy when the probe fails', async () => {
    vi.mocked(zigbeeApi.gateway).mockRejectedValue(new Error('nope'))
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(zigbeeApi.gateway).toHaveBeenCalled())
    expect(screen.getByText(/picks the gateway for you/i)).toBeDefined()
  })

  it('starts the import without overriding the configured gateway', async () => {
    mockGateway('zha', 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(zigbeeApi.gateway).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: /Start Zigbee scan/i }))

    await waitFor(() => expect(zigbeeApi.startImport).toHaveBeenCalledWith())
  })

  it('starts an import even when the gateway probe failed', async () => {
    vi.mocked(zigbeeApi.gateway).mockRejectedValue(new Error('nope'))
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(zigbeeApi.gateway).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: /Start Zigbee scan/i }))

    await waitFor(() => expect(zigbeeApi.startImport).toHaveBeenCalled())
  })

  it('toasts and closes on success', async () => {
    mockGateway('z2m', 'z2m', false)
    const onClose = vi.fn()
    const onImported = vi.fn()
    render(<ZigbeeImportModal open onClose={onClose} onImported={onImported} />)

    fireEvent.click(screen.getByRole('button', { name: /Start Zigbee scan/i }))

    await waitFor(() => expect(zigbeeApi.startImport).toHaveBeenCalled())
    expect(toast.success).toHaveBeenCalledWith(
      'Zigbee scan started — check Scan History for results',
    )
    expect(onImported).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('surfaces the WS error message on failure', async () => {
    mockGateway('zha', 'zha')
    vi.mocked(zigbeeApi.startImport).mockRejectedValue({
      error: { code: 'zha_not_configured', message: 'ZHA is not set up' },
    })
    render(<ZigbeeImportModal open onClose={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /Start Zigbee scan/i }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('ZHA is not set up'))
  })

  it('cancels without starting an import', async () => {
    mockGateway('z2m', 'z2m', false)
    const onClose = vi.fn()
    render(<ZigbeeImportModal open onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalled()
    expect(zigbeeApi.startImport).not.toHaveBeenCalled()
  })
})
