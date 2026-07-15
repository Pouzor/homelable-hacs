import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { CustomStyleModal } from '../CustomStyleModal'
import { useThemeStore } from '@/stores/themeStore'
import { useCanvasStore } from '@/stores/canvasStore'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }))

describe('CustomStyleModal', () => {
  beforeEach(() => {
    useThemeStore.setState({ activeTheme: 'default', customStyle: { nodes: {}, edges: {} } })
    useCanvasStore.setState({ markUnsaved: vi.fn() })
  })

  it('renders nothing when closed', () => {
    const { container } = render(<CustomStyleModal open={false} onClose={vi.fn()} />)
    expect(container.querySelector('[role="dialog"]')).toBeNull()
  })

  it('shows a placeholder until a type is selected', () => {
    render(<CustomStyleModal open onClose={vi.fn()} />)
    expect(screen.getByText(/Select a node type/)).toBeDefined()
  })

  it('initialNodeType preselects that type editor on open (NodeModal shortcut)', () => {
    render(<CustomStyleModal open initialNodeType="switch" onClose={vi.fn()} />)
    // Editor for Switch is shown immediately, no manual selection needed.
    expect(screen.getByText(/Apply to existing Switch/)).toBeDefined()
    expect(screen.queryByText(/Select a node type/)).toBeNull()
  })

  it('shows per-side default connection-point inputs in the node editor', () => {
    render(<CustomStyleModal open onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Router' }))
    expect(screen.getByText('Default connection points')).toBeDefined()
    expect(screen.getByLabelText('Top default connection points')).toBeDefined()
    expect(screen.getByLabelText('Left default connection points')).toBeDefined()
  })

  it('defaults per-side inputs to 1 (top/bottom) and 0 (left/right)', () => {
    render(<CustomStyleModal open onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Router' }))
    expect((screen.getByLabelText('Top default connection points') as HTMLInputElement).value).toBe('1')
    expect((screen.getByLabelText('Left default connection points') as HTMLInputElement).value).toBe('0')
  })

  it('editing a per-side default persists via setCustomStyle on Save', () => {
    const onClose = vi.fn()
    const setCustomStyle = vi.spyOn(useThemeStore.getState(), 'setCustomStyle')
    render(<CustomStyleModal open onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Router' }))
    fireEvent.change(screen.getByLabelText('Left default connection points'), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Custom Style' }))
    expect(setCustomStyle).toHaveBeenCalled()
    const saved = setCustomStyle.mock.calls.at(-1)?.[0]
    expect(saved?.nodes.router?.leftHandles).toBe(3)
  })

  it('edge editor exposes Start/End marker pickers defaulting to none', () => {
    render(<CustomStyleModal open onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edges' }))
    fireEvent.click(screen.getByRole('button', { name: /Ethernet/ }))
    const startNone = screen.getByRole('button', { name: 'Start marker none' })
    const endNone = screen.getByRole('button', { name: 'End marker none' })
    expect(startNone.getAttribute('aria-pressed')).toBe('true')
    expect(endNone.getAttribute('aria-pressed')).toBe('true')
  })

  it('picking an End shape feeds arrowEnd to applyTypeEdgeStyle', () => {
    const applyTypeEdgeStyle = vi.fn()
    useCanvasStore.setState({ applyTypeEdgeStyle })
    render(<CustomStyleModal open onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edges' }))
    fireEvent.click(screen.getByRole('button', { name: /Ethernet/ }))
    fireEvent.click(screen.getByRole('button', { name: 'End marker diamond' }))
    fireEvent.click(screen.getByRole('button', { name: /Apply to existing Ethernet/ }))
    expect(applyTypeEdgeStyle.mock.calls[0][1].arrowEnd).toBe('diamond')
    expect(applyTypeEdgeStyle.mock.calls[0][1].arrowStart).toBe('none')
  })

  it('picking a line style + width feeds lineStyle/widthMult to applyTypeEdgeStyle', () => {
    const applyTypeEdgeStyle = vi.fn()
    useCanvasStore.setState({ applyTypeEdgeStyle })
    render(<CustomStyleModal open onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edges' }))
    fireEvent.click(screen.getByRole('button', { name: /Ethernet/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Dotted' }))
    fireEvent.change(screen.getByRole('slider', { name: 'Line width multiplier' }), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: /Apply to existing Ethernet/ }))
    expect(applyTypeEdgeStyle.mock.calls[0][1].lineStyle).toBe('dotted')
    expect(applyTypeEdgeStyle.mock.calls[0][1].widthMult).toBe(3)
  })
})
