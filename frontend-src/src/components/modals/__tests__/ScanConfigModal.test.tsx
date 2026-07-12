import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ScanConfigModal } from '../ScanConfigModal'

vi.mock('@/api/client', () => ({
  scanApi: {
    getConfig: vi.fn(),
    trigger: vi.fn(),
  },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), message: vi.fn() } }))

import { scanApi } from '@/api/client'
import { toast } from 'sonner'

const defaultConfig = { data: { ranges: ['192.168.1.0/24'] } }

describe('ScanConfigModal', () => {
  beforeEach(() => {
    vi.mocked(scanApi.getConfig).mockResolvedValue(defaultConfig as never)
    vi.mocked(scanApi.trigger).mockReset()
    vi.mocked(scanApi.trigger).mockResolvedValue({} as never)
    vi.mocked(toast.success).mockReset()
    vi.mocked(toast.error).mockReset()
  })

  it('renders nothing when closed', () => {
    const { container } = render(<ScanConfigModal open={false} onClose={vi.fn()} onScanNow={vi.fn()} />)
    expect(container.querySelector('[role="dialog"]')).toBeNull()
  })

  it('loads config from API on open and shows ranges read-only', async () => {
    render(<ScanConfigModal open onClose={vi.fn()} onScanNow={vi.fn()} />)
    await waitFor(() => {
      expect(scanApi.getConfig).toHaveBeenCalledOnce()
    })
    const input = await screen.findByDisplayValue('192.168.1.0/24') as HTMLInputElement
    expect(input.disabled).toBe(true)
    expect(input.readOnly).toBe(true)
  })

  it('shows hint pointing to integration options', async () => {
    render(<ScanConfigModal open onClose={vi.fn()} onScanNow={vi.fn()} />)
    await waitFor(() => expect(scanApi.getConfig).toHaveBeenCalled())
    expect(screen.getByText(/Devices & services/)).toBeDefined()
  })

  it('disables Scan Now and shows empty state when no ranges configured', async () => {
    vi.mocked(scanApi.getConfig).mockResolvedValue({ data: { ranges: [] } } as never)
    render(<ScanConfigModal open onClose={vi.fn()} onScanNow={vi.fn()} />)
    await waitFor(() => expect(scanApi.getConfig).toHaveBeenCalled())
    expect(screen.getByText('No ranges configured.')).toBeDefined()
    const btn = screen.getByRole('button', { name: 'Scan Now' }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })

  it('triggers scan, calls onScanNow and closes on "Scan Now" click', async () => {
    const onScanNow = vi.fn()
    const onClose = vi.fn()
    render(<ScanConfigModal open onClose={onClose} onScanNow={onScanNow} />)
    await screen.findByDisplayValue('192.168.1.0/24')
    fireEvent.click(screen.getByRole('button', { name: 'Scan Now' }))
    await waitFor(() => {
      expect(scanApi.trigger).toHaveBeenCalledOnce()
      expect(onScanNow).toHaveBeenCalledOnce()
      expect(onClose).toHaveBeenCalledOnce()
    })
  })

  it('calls onClose when Cancel is clicked', async () => {
    const onClose = vi.fn()
    render(<ScanConfigModal open onClose={onClose} onScanNow={vi.fn()} />)
    await waitFor(() => expect(scanApi.getConfig).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledOnce()
  })

  // --- Deep scan (per-scan override; not persisted in HACS) ---

  it('reveals deep-scan fields when the section is toggled', async () => {
    render(<ScanConfigModal open onClose={vi.fn()} onScanNow={vi.fn()} />)
    await screen.findByDisplayValue('192.168.1.0/24')
    expect(screen.queryByText('Enable HTTP probe')).toBeNull()
    fireEvent.click(screen.getByText('Deep Scan'))
    expect(screen.getByText('Enable HTTP probe')).toBeDefined()
  })

  it('passes deep-scan overrides to trigger() as a per-scan override', async () => {
    render(<ScanConfigModal open onClose={vi.fn()} onScanNow={vi.fn()} />)
    await screen.findByDisplayValue('192.168.1.0/24')
    fireEvent.click(screen.getByText('Deep Scan'))
    fireEvent.change(screen.getByPlaceholderText('8000-8100, 9000-9100'), {
      target: { value: '8000-8100, 9000' },
    })
    fireEvent.click(screen.getByLabelText('Enable HTTP probe'))
    fireEvent.click(screen.getByRole('button', { name: 'Scan Now' }))
    await waitFor(() => {
      expect(scanApi.trigger).toHaveBeenCalledWith({
        http_ranges: ['8000-8100', '9000'],
        http_probe_enabled: true,
        verify_tls: false,
      })
    })
  })

  it('defaults deep-scan overrides to empty/off when the section is untouched', async () => {
    render(<ScanConfigModal open onClose={vi.fn()} onScanNow={vi.fn()} />)
    await screen.findByDisplayValue('192.168.1.0/24')
    fireEvent.click(screen.getByRole('button', { name: 'Scan Now' }))
    await waitFor(() => {
      expect(scanApi.trigger).toHaveBeenCalledWith({
        http_ranges: [],
        http_probe_enabled: false,
        verify_tls: false,
      })
    })
  })
})
