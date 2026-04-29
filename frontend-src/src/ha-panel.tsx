/**
 * Custom element registration for the Home Assistant Lovelace panel.
 *
 * HA mounts this element when the user navigates to the Homelable panel,
 * and pushes the `hass`, `narrow`, `route`, `panel` properties onto it.
 * We mirror `hass` into the module-level bridge so non-React code (api client)
 * can use it, and re-render React when it changes.
 */
import { StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import App from './App'
import { HassContext, setHass, type Hass } from './lib/hass'
import './index.css'

class HomelablePanel extends HTMLElement {
  private _hass: Hass | null = null
  private _root: Root | null = null
  private _mountPoint: HTMLDivElement | null = null

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

  // HA pushes these but we don't use them yet — accept silently.
  set narrow(_value: boolean) {}
  set route(_value: unknown) {}
  set panel(_value: unknown) {}

  connectedCallback() {
    if (!this._mountPoint) {
      this._mountPoint = document.createElement('div')
      this._mountPoint.style.cssText = 'width: 100%; height: 100%;'
      this.appendChild(this._mountPoint)
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
