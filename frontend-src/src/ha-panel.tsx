/**
 * Custom element registration for the Home Assistant Lovelace panel.
 *
 * HA mounts panels inside `home-assistant-main`'s shadow root, so `document.head`
 * stylesheets don't reach us. We attach our own shadow root and inject CSS into
 * it from two sources:
 *   - index.css / App.css via `?inline` (Tailwind v4 + theme vars; Tailwind's
 *     Vite plugin emits CSS through a path the inject-by-js plugin doesn't see)
 *   - CSS modules via window.__HOMELABLE_CSS__ stashed by cssInjectedByJsPlugin
 *     (see vite.config.ts)
 * `:root` is rewritten to `:host` so theme custom properties land on the shadow
 * host instead of `<html>` (which lives outside the boundary).
 */
import { StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import App from './App'
import { HassContext, setHass, type Hass } from './lib/hass'
import indexCss from './index.css?inline'
import appCss from './App.css?inline'

declare global {
  interface Window {
    __HOMELABLE_CSS__?: string
  }
}

class HomelablePanel extends HTMLElement {
  private _hass: Hass | null = null
  private _root: Root | null = null
  private _mountPoint: HTMLDivElement | null = null

  constructor() {
    super()
    this.attachShadow({ mode: 'open' })
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
    if (!shadow.querySelector('style[data-homelable]')) {
      const style = document.createElement('style')
      style.setAttribute('data-homelable', '')
      const combined = `${indexCss}\n${appCss}\n${window.__HOMELABLE_CSS__ ?? ''}`
      style.textContent = combined.replace(/:root\b/g, ':host')
      shadow.appendChild(style)
    }

    if (!this._mountPoint) {
      this._mountPoint = document.createElement('div')
      this._mountPoint.style.cssText = 'width: 100%; height: 100%;'
      shadow.appendChild(this._mountPoint)
    }
    if (this._hass) this._mount()
  }

  disconnectedCallback() {
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
          <App />
        </HassContext.Provider>
      </StrictMode>
    )
  }
}

if (!customElements.get('homelable-panel')) {
  customElements.define('homelable-panel', HomelablePanel)
}
