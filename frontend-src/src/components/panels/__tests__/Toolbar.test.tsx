import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Toolbar } from '../Toolbar'
import { useCanvasStore } from '@/stores/canvasStore'

vi.mock('@/stores/canvasStore')

vi.mock('@/components/ui/Logo', () => ({
  Logo: () => <div data-testid="logo" />,
}))

function mockStore(overrides: Partial<ReturnType<typeof useCanvasStore>> = {}) {
  vi.mocked(useCanvasStore).mockReturnValue({
    hasUnsavedChanges: false,
    past: [],
    future: [],
    ...overrides,
  } as ReturnType<typeof useCanvasStore>)
}

const defaultProps = {
  onSave: vi.fn(),
  onAutoLayout: vi.fn(),
  onExport: vi.fn(),
  onChangeStyle: vi.fn(),
  onUndo: vi.fn(),
  onRedo: vi.fn(),
  onShortcuts: vi.fn(),
  onExportMd: vi.fn(),
  onExportYaml: vi.fn(),
  onImportYaml: vi.fn(),
}

describe('Toolbar', () => {
  beforeEach(() => {
    mockStore()
    vi.clearAllMocks()
  })

  it('renders the Save button', () => {
    render(<Toolbar {...defaultProps} />)
    expect(screen.getByText('Save')).toBeInTheDocument()
  })

  it('calls onSave with no arguments when Save is clicked', () => {
    // Regression: wiring onClick={onSave} leaks the click event as
    // handleSave's designIdOverride, so the save targets a bogus design id
    // (the header Save button silently no-ops while Ctrl+S still works).
    const onSave = vi.fn()
    render(<Toolbar {...defaultProps} onSave={onSave} />)
    fireEvent.click(screen.getByText('Save'))
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith()
  })
})
