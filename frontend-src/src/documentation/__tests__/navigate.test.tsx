/**
 * How the rest of the panel reaches Documentation, and how a homepage canvas
 * link gets back out without navigating Home Assistant away from the panel.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { toast } from 'sonner'

import { Markdown } from '../markdown/Markdown'
import { designIdFromHref, openCanvas, openDocument, openDocumentation, openDocumentationForDevice } from '../navigate'
import { useDocsStore } from '../store'
import { useDesignStore } from '@/stores/designStore'
import { useUiStore } from '@/stores/uiStore'
import type { Design } from '@/types'

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

const designs = [
  { id: 'net', name: 'Home', design_type: 'network', icon: 'network' },
  { id: 'lab', name: 'Lab', design_type: 'network', icon: 'network' },
] as unknown as Design[]

beforeEach(() => {
  vi.clearAllMocks()
  useUiStore.setState({ view: 'canvas' })
  useDesignStore.getState().setDesigns(designs)
  useDesignStore.getState().setActiveDesign('net')
})

describe('designIdFromHref', () => {
  it('reads the design a homepage link names', () => {
    expect(designIdFromHref('?design=lab')).toBe('lab')
  })

  it('ignores anything that is not a query link', () => {
    expect(designIdFromHref(undefined)).toBeNull()
    expect(designIdFromHref('https://example.com/?design=lab')).toBeNull()
    expect(designIdFromHref('#section')).toBeNull()
    expect(designIdFromHref('?other=1')).toBeNull()
  })
})

describe('openCanvas', () => {
  it('switches design and leaves Documentation', () => {
    useUiStore.setState({ view: 'documentation' })
    openCanvas('lab')
    expect(useDesignStore.getState().activeDesignId).toBe('lab')
    expect(useUiStore.getState().view).toBe('canvas')
  })

  it('keeps the active design when the link names one that is gone', () => {
    openCanvas('deleted')
    expect(useDesignStore.getState().activeDesignId).toBe('net')
    expect(useUiStore.getState().view).toBe('canvas')
  })
})

describe('opening a document', () => {
  it('opens a device document in the Documentation section', async () => {
    const openForDevice = vi.fn().mockResolvedValue(true)
    useDocsStore.setState({ openForDevice })
    await openDocumentationForDevice('dev-1', 'NAS')
    expect(useUiStore.getState().view).toBe('documentation')
    expect(openForDevice).toHaveBeenCalledWith('dev-1', 'NAS')
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('says so when the device document cannot be opened', async () => {
    useDocsStore.setState({ openForDevice: vi.fn().mockResolvedValue(false) })
    await openDocumentationForDevice('dev-1', 'NAS')
    expect(toast.error).toHaveBeenCalled()
  })

  it('lands on the homepage when entering from the Sidebar (homelable #583)', async () => {
    const openHome = vi.fn().mockResolvedValue(true)
    useDocsStore.setState({ openHome })
    await openDocumentation()
    expect(useUiStore.getState().view).toBe('documentation')
    expect(openHome).toHaveBeenCalled()
  })

  it('opens a document by id', async () => {
    const open = vi.fn().mockResolvedValue(undefined)
    useDocsStore.setState({ open })
    await openDocument('d1')
    expect(useUiStore.getState().view).toBe('documentation')
    expect(open).toHaveBeenCalledWith('d1')
  })
})

describe('Markdown — canvas links', () => {
  it('turns a homepage canvas link into a design switch', async () => {
    useUiStore.setState({ view: 'documentation' })
    render(<Markdown body="- [Lab](?design=lab) — network" />)
    fireEvent.click(screen.getByRole('link', { name: 'Lab' }))
    await waitFor(() => expect(useUiStore.getState().view).toBe('canvas'))
    expect(useDesignStore.getState().activeDesignId).toBe('lab')
  })

  it('leaves an ordinary link alone', () => {
    render(<Markdown body="[docs](https://example.com)" />)
    const link = screen.getByRole('link', { name: 'docs' })
    expect(link).toHaveAttribute('target', '_blank')
    expect(useUiStore.getState().view).toBe('canvas')
  })
})
