import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Toolbar } from '../Toolbar'
import { useCanvasStore } from '@/stores/canvasStore'
import { useDesignStore } from '@/stores/designStore'
import { useRackStore } from '@/rack/store'

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

describe('Toolbar — rack canvas', () => {
  beforeEach(() => {
    mockStore()
    vi.clearAllMocks()
    useRackStore.getState().reset()
    useDesignStore.setState({ activeDesignType: 'rack' })
  })

  afterEach(() => {
    useDesignStore.setState({ activeDesignType: null })
  })

  it('shows the rack actions instead of the logical canvas ones', () => {
    render(<Toolbar {...defaultProps} />)
    expect(screen.getByText('Rack')).toBeInTheDocument()
    expect(screen.getByText('Import links')).toBeInTheDocument()
    expect(screen.queryByText('Auto Layout')).not.toBeInTheDocument()
    expect(screen.queryByText('MD')).not.toBeInTheDocument()
    expect(screen.queryByTitle('Undo (Ctrl+Z)')).not.toBeInTheDocument()
    // PNG capture is DOM-based, so it stays.
    expect(screen.getByText('PNG')).toBeInTheDocument()
  })
})
