/**
 * Custom element registration for the Home Assistant Lovelace panel.
 *
 * HA mounts panels inside `home-assistant-main`'s shadow root, so `document.head`
 * stylesheets don't reach us — we attach our own shadow root and inject our CSS
 * into it (see `lib/shadowCss.ts`).
 */
import { StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import App from './App'
import { HassContext, setHass, type Hass } from './lib/hass'
import { ShadowRootContext } from './lib/portal'
import { injectShadowStyles } from './lib/shadowCss'

class HomelablePanel extends HTMLElement {
  private _hass: Hass | null = null
  private _root: Root | null = null
  private _mountPoint: HTMLDivElement | null = null
  private _resizeObserver: ResizeObserver | null = null
  private _onResize = () => this._applyHeight()

  constructor() {
    super()
    this.attachShadow({ mode: 'open' })
  }

  /**
   * `height: 100%` only resolves when every ancestor up to <html> has a
   * resolved height. Depending on the HA version and the browser the panel
   * container is sometimes laid out with `height: auto`, and the panel then
   * collapses to its content instead of filling the window. Measure the space
   * actually left below the host and pin it in pixels — that stays correct
   * whether or not HA renders a toolbar above us.
   */
  private _applyHeight() {
    if (!this.isConnected) return
    const top = this.getBoundingClientRect().top
    const available = Math.max(0, window.innerHeight - top)
    if (available > 0) this.style.height = `${available}px`
  }

  set hass(value: Hass) {
    const first = this._hass === null
    this._hass = value
    setHass(value)
    if (first) this._mount()
    else this._render()
  }

  get hass(): Hass | null {
    return this._hass
  }

  set narrow(_value: boolean) {}
  set route(_value: unknown) {}
  set panel(_value: unknown) {}

  connectedCallback() {
    // The host element itself is inline by default — make it fill its slot.
    this.style.cssText = 'display: block; width: 100%; height: 100%;'

    const shadow = this.shadowRoot!
    injectShadowStyles(shadow)

    if (!this._mountPoint) {
      this._mountPoint = document.createElement('div')
      this._mountPoint.style.cssText = 'width: 100%; height: 100%;'
      shadow.appendChild(this._mountPoint)
    }
    this._applyHeight()
    window.addEventListener('resize', this._onResize)
    if (typeof ResizeObserver !== 'undefined' && !this._resizeObserver) {
      // The host moves vertically when HA shows/hides its own chrome.
      this._resizeObserver = new ResizeObserver(this._onResize)
      this._resizeObserver.observe(document.documentElement)
    }

    if (this._hass) this._mount()
  }

  disconnectedCallback() {
    window.removeEventListener('resize', this._onResize)
    this._resizeObserver?.disconnect()
    this._resizeObserver = null
    if (this._root) {
      this._root.unmount()
      this._root = null
    }
    setHass(null)
  }

  private _mount() {
    if (!this._mountPoint || this._root) {
      this._render()
      return
    }
    this._root = createRoot(this._mountPoint)
    this._render()
  }

  private _render() {
    if (!this._root || !this._hass) return
    this._root.render(
      <StrictMode>
        <HassContext.Provider value={this._hass}>
          <ShadowRootContext.Provider value={this._mountPoint}>
            <App />
          </ShadowRootContext.Provider>
        </HassContext.Provider>
      </StrictMode>
    )
  }
}

if (!customElements.get('homelable-panel')) {
  customElements.define('homelable-panel', HomelablePanel)
}
