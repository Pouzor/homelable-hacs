import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ShortcutsModal } from '../ShortcutsModal'

describe('ShortcutsModal', () => {
  it('renders nothing when closed', () => {
    const { container } = render(<ShortcutsModal open={false} onClose={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders shortcut groups when open', () => {
    render(<ShortcutsModal open={true} onClose={vi.fn()} />)
    expect(screen.getByText('Keyboard Shortcuts')).toBeDefined()
    expect(screen.getByText('Canvas')).toBeDefined()
    expect(screen.getByText('Nodes')).toBeDefined()
    expect(screen.getByText('Navigation')).toBeDefined()
  })

  it('shows key shortcuts in kbd elements', () => {
    render(<ShortcutsModal open={true} onClose={vi.fn()} />)
    expect(screen.getAllByText('Ctrl').length).toBeGreaterThan(0)
  })

  it('calls onClose when backdrop clicked', () => {
    const onClose = vi.fn()
    const { container } = render(<ShortcutsModal open={true} onClose={onClose} />)
    fireEvent.click(container.firstChild as HTMLElement)
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose when X button clicked', () => {
    const onClose = vi.fn()
    render(<ShortcutsModal open={true} onClose={onClose} />)
    const buttons = screen.getAllByRole('button')
    fireEvent.click(buttons[0])
    expect(onClose).toHaveBeenCalled()
  })
})
