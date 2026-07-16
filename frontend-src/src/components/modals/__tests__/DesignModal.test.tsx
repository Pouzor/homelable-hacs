import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DesignModal, type DesignFormData } from '../DesignModal'
import { DEFAULT_DESIGN_ICON } from '@/utils/designIcons'
import type { Design } from '@/types'

function renderModal(props: Partial<React.ComponentProps<typeof DesignModal>> = {}) {
  const onSubmit = vi.fn<(data: DesignFormData) => void>()
  const onClose = vi.fn()
  render(
    <DesignModal
      open
      onClose={onClose}
      onSubmit={onSubmit}
      {...props}
    />,
  )
  return { onSubmit, onClose }
}

describe('DesignModal', () => {
  it('submits name + icon for a blank canvas', () => {
    const { onSubmit } = renderModal()
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Fresh' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    expect(onSubmit).toHaveBeenCalledWith({ name: 'Fresh', icon: DEFAULT_DESIGN_ICON })
  })

  describe('copy from existing', () => {
    const sourceDesigns: Design[] = [
      { id: 's1', name: 'Home Net', icon: 'network', design_type: 'network',
        created_at: '', updated_at: '', node_count: 4, group_count: 1, text_count: 2 },
      { id: 's2', name: 'Lab', icon: 'server', design_type: 'network',
        created_at: '', updated_at: '', node_count: 7, group_count: 0, text_count: 0 },
    ]

    it('offers no copy option when there are no source designs', () => {
      renderModal({ sourceDesigns: [] })
      expect(screen.queryByRole('button', { name: 'Copy from existing' })).toBeNull()
    })

    it('shows the source list with counts once "Copy from existing" is chosen', () => {
      renderModal({ sourceDesigns })
      // Hidden until the user opts into copying.
      expect(screen.queryByText('Home Net')).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: 'Copy from existing' }))
      expect(screen.getByText('Home Net')).toBeDefined()
      expect(screen.getByText('4 nodes · 1 groups · 2 text')).toBeDefined()
      expect(screen.getByText('7 nodes · 0 groups · 0 text')).toBeDefined()
    })

    it('includes sourceId (first design by default) on submit', () => {
      const { onSubmit } = renderModal({ sourceDesigns })
      fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Copy of Home' } })
      fireEvent.click(screen.getByRole('button', { name: 'Copy from existing' }))
      fireEvent.click(screen.getByRole('button', { name: 'Create' }))
      expect(onSubmit).toHaveBeenCalledWith({ name: 'Copy of Home', icon: DEFAULT_DESIGN_ICON, sourceId: 's1' })
    })

    it('includes the picked sourceId on submit', () => {
      const { onSubmit } = renderModal({ sourceDesigns })
      fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Copy of Lab' } })
      fireEvent.click(screen.getByRole('button', { name: 'Copy from existing' }))
      fireEvent.click(screen.getByRole('radio', { name: /Lab/ }))
      fireEvent.click(screen.getByRole('button', { name: 'Create' }))
      expect(onSubmit).toHaveBeenCalledWith({ name: 'Copy of Lab', icon: DEFAULT_DESIGN_ICON, sourceId: 's2' })
    })

    it('omits sourceId when the blank option is kept', () => {
      const { onSubmit } = renderModal({ sourceDesigns })
      fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Fresh' } })
      fireEvent.click(screen.getByRole('button', { name: 'Create' }))
      expect(onSubmit).toHaveBeenCalledWith({ name: 'Fresh', icon: DEFAULT_DESIGN_ICON })
      expect('sourceId' in onSubmit.mock.calls[0][0]).toBe(false)
    })
  })
})
