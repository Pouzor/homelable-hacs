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

  describe('floor plan section', () => {
    const fm = {
      imageData: '/homelable_media/abc.png',
      posX: 40, posY: 60, width: 800, height: 600,
      opacity: 0.8, locked: false, enabled: true,
    }

    it('is hidden by default and submit omits floorMap', () => {
      const { onSubmit } = renderModal()
      expect(screen.queryByText('Floor Plan')).toBeNull()
      fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'X' } })
      fireEvent.click(screen.getByRole('button', { name: 'Create' }))
      expect(onSubmit).toHaveBeenCalledWith({ name: 'X', icon: DEFAULT_DESIGN_ICON })
      expect('floorMap' in onSubmit.mock.calls[0][0]).toBe(false)
    })

    it('shows the section and preserves position while updating config', () => {
      const { onSubmit } = renderModal({
        showFloorMap: true,
        initialFloorMap: fm,
        initial: { name: 'Home', icon: DEFAULT_DESIGN_ICON },
        submitLabel: 'Save',
      })
      expect(screen.getByText('Floor Plan')).toBeDefined()
      expect(screen.getByAltText('Floor plan preview')).toBeDefined()

      // Toggle "Show on canvas" off.
      const enabledBox = screen.getByLabelText('Show on canvas') as HTMLInputElement
      fireEvent.click(enabledBox)

      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      expect(onSubmit).toHaveBeenCalledWith({
        name: 'Home',
        icon: DEFAULT_DESIGN_ICON,
        floorMap: { ...fm, enabled: false },
      })
    })

    it('submits floorMap: null when the image is removed', () => {
      const { onSubmit } = renderModal({
        showFloorMap: true,
        initialFloorMap: fm,
        initial: { name: 'Home', icon: DEFAULT_DESIGN_ICON },
        submitLabel: 'Save',
      })
      fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
      expect(screen.queryByAltText('Floor plan preview')).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      expect(onSubmit).toHaveBeenCalledWith({
        name: 'Home',
        icon: DEFAULT_DESIGN_ICON,
        floorMap: null,
      })
    })

    it('uploads a chosen file and stores the returned server URL', async () => {
      const onUploadImage = vi.fn().mockResolvedValue('/homelable_media/deadbeef.png')
      const { onSubmit } = renderModal({
        showFloorMap: true,
        initialFloorMap: null,
        initial: { name: 'Home', icon: DEFAULT_DESIGN_ICON },
        submitLabel: 'Save',
        onUploadImage,
      })
      const file = new File(['x'], 'plan.png', { type: 'image/png' })
      const input = document.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(input, { target: { files: [file] } })

      await screen.findByAltText('Floor plan preview')
      expect(onUploadImage).toHaveBeenCalledWith(file)

      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      const submitted = onSubmit.mock.calls[0][0]
      expect(submitted.floorMap.imageData).toBe('/homelable_media/deadbeef.png')
    })

    it('leaves state untouched when upload fails', async () => {
      const onUploadImage = vi.fn().mockRejectedValue(new Error('boom'))
      renderModal({
        showFloorMap: true,
        initialFloorMap: null,
        initial: { name: 'Home', icon: DEFAULT_DESIGN_ICON },
        submitLabel: 'Save',
        onUploadImage,
      })
      const file = new File(['x'], 'plan.png', { type: 'image/png' })
      const input = document.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(input, { target: { files: [file] } })
      await vi.waitFor(() => expect(onUploadImage).toHaveBeenCalled())
      expect(screen.queryByAltText('Floor plan preview')).toBeNull()
    })

    // Regression: reopening the edit modal after a canvas-side resize must not
    // save stale dimensions. Sidebar bumps the modal `key` on every open so it
    // remounts and re-seeds from the current floor plan.
    it('re-seeds width/height when remounted with a new key (reopen after resize)', () => {
      const onSubmit = vi.fn()
      const initial = { name: 'Home', icon: DEFAULT_DESIGN_ICON }
      const { rerender } = render(
        <DesignModal key="k1" open onClose={vi.fn()} onSubmit={onSubmit}
          showFloorMap initialFloorMap={fm} initial={initial} submitLabel="Save" />,
      )
      // Canvas-side resize happened; reopen with a fresh key + larger dims.
      const resized = { ...fm, width: 1200, height: 900 }
      rerender(
        <DesignModal key="k2" open onClose={vi.fn()} onSubmit={onSubmit}
          showFloorMap initialFloorMap={resized} initial={initial} submitLabel="Save" />,
      )
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      expect(onSubmit.mock.calls[0][0].floorMap).toMatchObject({ width: 1200, height: 900 })
    })

    it('keeps stale dimensions when reopened without remount (why the key bump matters)', () => {
      const onSubmit = vi.fn()
      const initial = { name: 'Home', icon: DEFAULT_DESIGN_ICON }
      const { rerender } = render(
        <DesignModal key="same" open onClose={vi.fn()} onSubmit={onSubmit}
          showFloorMap initialFloorMap={fm} initial={initial} submitLabel="Save" />,
      )
      const resized = { ...fm, width: 1200, height: 900 }
      rerender(
        <DesignModal key="same" open onClose={vi.fn()} onSubmit={onSubmit}
          showFloorMap initialFloorMap={resized} initial={initial} submitLabel="Save" />,
      )
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      // Same key → no remount → local state still the original 800×600.
      expect(onSubmit.mock.calls[0][0].floorMap).toMatchObject({ width: 800, height: 600 })
    })

    it('submits floorMap: null when shown but no image was chosen', () => {
      const { onSubmit } = renderModal({
        showFloorMap: true,
        initialFloorMap: null,
        submitLabel: 'Save',
      })
      fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Empty' } })
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      expect(onSubmit).toHaveBeenCalledWith({ name: 'Empty', icon: DEFAULT_DESIGN_ICON, floorMap: null })
    })
  })
})
