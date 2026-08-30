/**
 * The card's config element. `ha-form` belongs to the HA frontend and does not
 * exist in jsdom, so it stays an inert element here and the assertions are on
 * the props handed to it and on the events sent back to Lovelace.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import type { FormSchemaEntry } from '@/lib/cardEditorForm'

await import('../card-editor')

interface HaForm extends HTMLElement {
  schema?: FormSchemaEntry[]
  data?: Record<string, unknown>
  hass?: unknown
}

interface Editor extends HTMLElement {
  setConfig: (config: Record<string, unknown>) => void
  hass: unknown
}

const BASE = { type: 'custom:homelable-canvas-card' }

const sendMessagePromise = vi.fn()

function hass() {
  return { connection: { sendMessagePromise }, callWS: sendMessagePromise }
}

/** Mount an editor, feed it a config and hass, and let the design list settle. */
async function mount(config: Record<string, unknown> = BASE) {
  const editor = document.createElement('homelable-canvas-card-editor') as Editor
  document.body.appendChild(editor)
  editor.setConfig(config)
  editor.hass = hass()
  await vi.waitFor(() => expect(editor.querySelector('ha-form')).not.toBeNull())
  return { editor, form: editor.querySelector('ha-form') as HaForm }
}

function emitValue(form: HaForm, value: Record<string, unknown>) {
  form.dispatchEvent(new CustomEvent('value-changed', { detail: { value } }))
}

describe('<homelable-canvas-card-editor>', () => {
  beforeEach(() => {
    document.body.replaceChildren()
    sendMessagePromise.mockReset()
    sendMessagePromise.mockResolvedValue({ designs: [{ id: 'a', name: 'Home' }] })
  })

  it('is registered as a custom element', () => {
    expect(customElements.get('homelable-canvas-card-editor')).toBeDefined()
  })

  it('is what the card hands Lovelace', async () => {
    await import('@/ha-card')
    // The card class isn't exported; reach it through its registration.
    const card = customElements.get('homelable-canvas-card') as unknown as {
      getConfigElement: () => HTMLElement
    }
    expect(card.getConfigElement().localName).toBe('homelable-canvas-card-editor')
  })

  it('populates the design dropdown from the WS command', async () => {
    const { form } = await mount()
    expect(sendMessagePromise).toHaveBeenCalledWith({ type: 'homelable/designs/list' })
    await vi.waitFor(() => {
      const design = form.schema!.find((entry) => entry.name === 'design_id')!
      expect(design.selector.select).toMatchObject({
        options: [{ value: 'a', label: 'Home' }],
      })
    })
  })

  it('falls back to a text field when the design list cannot be loaded', async () => {
    sendMessagePromise.mockRejectedValue(new Error('ws down'))
    const { form } = await mount()
    await vi.waitFor(() => {
      const design = form.schema!.find((entry) => entry.name === 'design_id')!
      expect(design.selector.text).toEqual({})
    })
  })

  it('fills the form with the stored config', async () => {
    const { form } = await mount({ ...BASE, height: 700, title: 'Network' })
    expect(form.data).toMatchObject({ height: 700, title: 'Network', interactive: 'pan' })
  })

  it('emits config-changed with the whole config', async () => {
    const { editor, form } = await mount()
    const changed = vi.fn()
    editor.addEventListener('config-changed', (e) => changed((e as CustomEvent).detail.config))

    emitValue(form, { height: 700, interactive: 'none' })

    expect(changed).toHaveBeenCalledWith({
      ...BASE,
      height: 700,
      interactive: 'none',
    })
  })

  it('keeps the dashboard layout across an edit', async () => {
    const previous = { ...BASE, grid_options: { columns: 'full' } }
    const { editor, form } = await mount(previous)
    const changed = vi.fn()
    editor.addEventListener('config-changed', (e) => changed((e as CustomEvent).detail.config))

    emitValue(form, { title: 'Network' })

    expect(changed.mock.calls[0][0].grid_options).toEqual({ columns: 'full' })
  })

  it('bubbles the event, as Lovelace listens on an ancestor', async () => {
    const { form } = await mount()
    const changed = vi.fn()
    document.body.addEventListener('config-changed', changed)

    emitValue(form, { title: 'Network' })

    expect(changed).toHaveBeenCalled()
    document.body.removeEventListener('config-changed', changed)
  })

  it('ignores a value-changed carrying nothing', async () => {
    const { editor, form } = await mount()
    const changed = vi.fn()
    editor.addEventListener('config-changed', changed)

    form.dispatchEvent(new CustomEvent('value-changed', { detail: {} }))

    expect(changed).not.toHaveBeenCalled()
  })

  it('asks for the design list only once', async () => {
    const { editor } = await mount()
    editor.hass = hass()
    editor.hass = hass()
    expect(sendMessagePromise).toHaveBeenCalledTimes(1)
  })

  it('renders nothing before hass arrives', () => {
    const editor = document.createElement('homelable-canvas-card-editor') as Editor
    document.body.appendChild(editor)
    editor.setConfig(BASE)
    expect(editor.querySelector('ha-form')).toBeNull()
  })
})
