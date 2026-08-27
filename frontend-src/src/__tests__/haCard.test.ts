/**
 * <homelable-canvas-card> element behaviour. The React half is mocked out —
 * the point here is the custom-element contract HA relies on (setConfig,
 * getCardSize, hass, the card picker entry) and the single-primary rule.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import type { Hass } from '@/lib/hass'

const render = vi.fn()
const unmount = vi.fn()

vi.mock('@/lib/cardMount', () => ({
  mountCard: vi.fn(() => ({ render, unmount })),
}))

await import('../ha-card')

const HASS = { connection: {} } as unknown as Hass

/** Attach a card, feed it a config and hass, and wait for the dynamic import. */
async function addCard(config: Record<string, unknown> = {}) {
  const card = document.createElement('homelable-canvas-card') as HTMLElement & {
    setConfig: (c: unknown) => void
    getCardSize: () => number
    hass: Hass | null
  }
  document.body.appendChild(card)
  card.setConfig({ type: 'custom:homelable-canvas-card', ...config })
  card.hass = HASS
  await vi.waitFor(() => expect(render).toHaveBeenCalled())
  return card
}

describe('<homelable-canvas-card>', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    render.mockClear()
    unmount.mockClear()
  })

  it('is registered as a custom element', () => {
    expect(customElements.get('homelable-canvas-card')).toBeDefined()
  })

  it('registers itself in the HA card picker exactly once', () => {
    const cards = (window as unknown as { customCards: Array<{ type: string }> }).customCards
    expect(cards.filter((c) => c.type === 'homelable-canvas-card')).toHaveLength(1)
  })

  it('renders with the parsed config once hass arrives', async () => {
    await addCard({ title: 'Network', height: 500 })
    const [hass, config, state] = render.mock.calls.at(-1)!
    expect(hass).toBe(HASS)
    expect(config).toMatchObject({ title: 'Network', height: 500, interactive: 'pan' })
    expect(state).toEqual({ primary: true })
  })

  it('does not render before a config is set', async () => {
    const card = document.createElement('homelable-canvas-card') as HTMLElement & { hass: Hass }
    document.body.appendChild(card)
    card.hass = HASS
    await Promise.resolve()
    expect(render).not.toHaveBeenCalled()
  })

  it('rejects an invalid config by throwing, as Lovelace expects', () => {
    const card = document.createElement('homelable-canvas-card') as HTMLElement & {
      setConfig: (c: unknown) => void
    }
    expect(() => card.setConfig({ interactive: 'zoom' })).toThrow(/interactive/)
  })

  it('reports its height in Lovelace rows', async () => {
    const card = await addCard({ height: 500 })
    expect(card.getCardSize()).toBe(10)
  })

  it('marks only the first connected card as primary', async () => {
    await addCard()
    render.mockClear()
    await addCard()
    // Both cards re-render; the newcomer is not the primary one.
    const states = render.mock.calls.map(([, , state]) => state.primary)
    expect(states).toContain(true)
    expect(states).toContain(false)
    expect(render.mock.calls.at(-1)![2]).toEqual({ primary: false })
  })

  it('promotes the next card when the primary one is removed', async () => {
    const first = await addCard()
    const second = await addCard()
    render.mockClear()
    first.remove()
    expect(unmount).toHaveBeenCalled()
    await vi.waitFor(() => expect(render).toHaveBeenCalled())
    expect(render.mock.calls.at(-1)![2]).toEqual({ primary: true })
    expect(second.isConnected).toBe(true)
  })

  it('unmounts its React root when detached', async () => {
    const card = await addCard()
    card.remove()
    expect(unmount).toHaveBeenCalledTimes(1)
  })
})
