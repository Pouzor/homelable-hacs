import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ZigbeeImportModal } from '../ZigbeeImportModal'
import type { ZigbeeBackend, ZigbeeBackends } from '../types'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/api/ha', () => ({
  zigbeeApi: {
    backends: vi.fn(),
    startImport: vi.fn(),
  },
}))

import { zigbeeApi } from '@/api/ha'
import { toast } from 'sonner'

function mockBackends(zha: boolean, z2m: boolean, def: ZigbeeBackend) {
  const data: ZigbeeBackends = { zha, z2m, default: def }
  vi.mocked(zigbeeApi.backends).mockResolvedValue({ data } as never)
}

describe('ZigbeeImportModal', () => {
  beforeEach(() => {
    vi.mocked(toast.success).mockReset()
    vi.mocked(toast.error).mockReset()
    vi.mocked(zigbeeApi.backends).mockReset()
    vi.mocked(zigbeeApi.startImport).mockReset()
    vi.mocked(zigbeeApi.startImport).mockResolvedValue({
      data: { run_id: 'r1', status: 'running', devices_found: 0, backend: 'zha' },
    } as never)
  })

  it('renders the import dialog when open', async () => {
    mockBackends(false, true, 'z2m')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    expect(screen.getByText('Zigbee Import')).toBeDefined()
    expect(screen.getByRole('button', { name: /Start Zigbee scan/i })).toBeDefined()
    await waitFor(() => expect(zigbeeApi.backends).toHaveBeenCalled())
  })

  it('names both gateways whichever one is in use', async () => {
    mockBackends(true, false, 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(zigbeeApi.backends).toHaveBeenCalled())
    expect(screen.getAllByText('ZHA').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Zigbee2MQTT').length).toBeGreaterThan(0)
  })

  it('shows the detected gateway instead of a picker when only one is available', async () => {
    mockBackends(true, false, 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/Detected gateway/i)).toBeDefined())
    expect(screen.queryByRole('button', { name: 'Zigbee2MQTT' })).toBeNull()
    // ZHA-specific copy: no broker, near-instant.
    expect(screen.getByText(/no MQTT broker/i)).toBeDefined()
  })

  it('never shows Z2M-only copy to a ZHA user', async () => {
    mockBackends(true, false, 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/Detected gateway/i)).toBeDefined())
    expect(screen.queryByText(/base topic/i)).toBeNull()
  })

  it('claims no gateway until the probe answers', () => {
    mockBackends(true, false, 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    // Rendered before the probe resolves: no backend asserted either way.
    expect(screen.getByText(/checking/i)).toBeDefined()
    expect(screen.queryByText(/base topic/i)).toBeNull()
    expect(screen.queryByText(/no MQTT broker/i)).toBeNull()
  })

  it('falls back to neutral copy when the probe fails', async () => {
    vi.mocked(zigbeeApi.backends).mockRejectedValue(new Error('nope'))
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(zigbeeApi.backends).toHaveBeenCalled())
    expect(screen.getByText(/picks the gateway for you/i)).toBeDefined()
  })

  it('offers a backend picker when both gateways are available', async () => {
    mockBackends(true, true, 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Zigbee2MQTT' })).toBeDefined(),
    )
    expect(screen.getByRole('button', { name: 'ZHA' })).toBeDefined()
  })

  it('starts the import with the picked backend', async () => {
    mockBackends(true, true, 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Zigbee2MQTT' })).toBeDefined(),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Zigbee2MQTT' }))
    fireEvent.click(screen.getByRole('button', { name: /Start Zigbee scan/i }))

    await waitFor(() => expect(zigbeeApi.startImport).toHaveBeenCalledWith('z2m'))
  })

  it('starts with the integration default when the picker is hidden', async () => {
    mockBackends(true, false, 'zha')
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(zigbeeApi.backends).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: /Start Zigbee scan/i }))

    await waitFor(() => expect(zigbeeApi.startImport).toHaveBeenCalledWith('zha'))
  })

  it('still starts an import when the backend probe fails', async () => {
    vi.mocked(zigbeeApi.backends).mockRejectedValue(new Error('nope'))
    render(<ZigbeeImportModal open onClose={vi.fn()} />)
    await waitFor(() => expect(zigbeeApi.backends).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: /Start Zigbee scan/i }))

    // No backend known → let the integration choose.
    await waitFor(() => expect(zigbeeApi.startImport).toHaveBeenCalledWith(undefined))
  })

  it('toasts and closes on success', async () => {
    mockBackends(false, true, 'z2m')
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
    mockBackends(true, false, 'zha')
    vi.mocked(zigbeeApi.startImport).mockRejectedValue({
      error: { code: 'zha_not_configured', message: 'ZHA is not set up' },
    })
    render(<ZigbeeImportModal open onClose={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /Start Zigbee scan/i }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('ZHA is not set up'))
  })

  it('cancels without starting an import', async () => {
    mockBackends(false, true, 'z2m')
    const onClose = vi.fn()
    render(<ZigbeeImportModal open onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalled()
    expect(zigbeeApi.startImport).not.toHaveBeenCalled()
  })
})
