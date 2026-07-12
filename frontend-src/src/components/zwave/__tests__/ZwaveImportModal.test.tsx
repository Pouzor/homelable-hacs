import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ZwaveImportModal } from '../ZwaveImportModal'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/api/ha', () => ({
  zwaveApi: {
    startImport: vi.fn(),
  },
}))

import { zwaveApi } from '@/api/ha'
import { toast } from 'sonner'

describe('ZwaveImportModal', () => {
  beforeEach(() => {
    vi.mocked(toast.success).mockReset()
    vi.mocked(toast.error).mockReset()
    vi.mocked(zwaveApi.startImport).mockReset()
  })

  it('renders the Z-Wave import dialog when open', () => {
    render(<ZwaveImportModal open onClose={vi.fn()} />)
    expect(screen.getByText('Z-Wave JS UI Import')).toBeDefined()
    expect(screen.getByRole('button', { name: /Start Z-Wave scan/i })).toBeDefined()
  })

  it('starts the background import, toasts, and closes on success', async () => {
    vi.mocked(zwaveApi.startImport).mockResolvedValue({
      data: { run_id: 'r1', status: 'running', devices_found: 0 },
    } as never)
    const onClose = vi.fn()
    const onImported = vi.fn()
    render(<ZwaveImportModal open onClose={onClose} onImported={onImported} />)

    fireEvent.click(screen.getByRole('button', { name: /Start Z-Wave scan/i }))

    await waitFor(() => expect(zwaveApi.startImport).toHaveBeenCalled())
    expect(toast.success).toHaveBeenCalledWith(
      'Z-Wave scan started — check Scan History for results',
    )
    expect(onImported).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('surfaces the WS error message on failure', async () => {
    vi.mocked(zwaveApi.startImport).mockRejectedValue({
      error: { code: 'mqtt_not_configured', message: 'MQTT not configured' },
    })
    render(<ZwaveImportModal open onClose={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /Start Z-Wave scan/i }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('MQTT not configured'),
    )
  })

  it('cancels without starting an import', () => {
    const onClose = vi.fn()
    render(<ZwaveImportModal open onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalled()
    expect(zwaveApi.startImport).not.toHaveBeenCalled()
  })
})
