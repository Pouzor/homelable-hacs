import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'
import '../ha-panel'

/**
 * The panel must fill the window even when its HA container is laid out with
 * `height: auto` (observed on Windows, where the panel collapsed to its
 * content height).
 */
describe('homelable-panel host sizing', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'innerHeight', { value: 900, writable: true })
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  function mount(top: number) {
    const el = document.createElement('homelable-panel')
    vi.spyOn(el, 'getBoundingClientRect').mockReturnValue({ top } as DOMRect)
    document.body.appendChild(el)
    return el
  }

  it('pins the host height to the space left below it', () => {
    const el = mount(56)
    expect(el.style.height).toBe('844px')
  })

  it('fills the full window when no HA chrome sits above it', () => {
    const el = mount(0)
    expect(el.style.height).toBe('900px')
  })

  it('recomputes on window resize', () => {
    const el = mount(0)
    ;(window as unknown as { innerHeight: number }).innerHeight = 500
    window.dispatchEvent(new Event('resize'))
    expect(el.style.height).toBe('500px')
  })

  it('stops listening once disconnected', () => {
    const el = mount(0)
    el.remove()
    ;(window as unknown as { innerHeight: number }).innerHeight = 400
    window.dispatchEvent(new Event('resize'))
    expect(el.style.height).toBe('900px')
  })
})
