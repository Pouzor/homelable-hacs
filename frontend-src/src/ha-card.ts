/**
 * Build entry for the Lovelace card — registers <homelable-canvas-card>.
 *
 * HA loads this bundle as a module on *every* frontend page (panel.py calls
 * frontend.add_extra_js_url), so it stays deliberately tiny: no React, no
 * Tailwind, no canvas. Everything heavy lives behind the dynamic import in
 * `connectedCallback`, which only runs once a card is actually on a dashboard.
 * Keep it that way — a static import of anything under `lib/` other than the
 * type below would pull React back into every page load.
 *
 * Skeleton: config handling and the read-only canvas land next
 * (see TODO-002 steps 3 and 4).
 */
import type { Hass } from './lib/hass'
import type { CardMount } from './lib/cardMount'

class HomelableCanvasCard extends HTMLElement {
  private _hass: Hass | null = null
  private _mount: CardMount | null = null
  /** Guards against a disconnect that lands before the dynamic import settles. */
  private _connected = false

  constructor() {
    super()
    this.attachShadow({ mode: 'open' })
  }

  set hass(value: Hass) {
    this._hass = value
    this._mount?.render(value)
  }

  get hass(): Hass | null {
    return this._hass
  }

  connectedCallback() {
    this.style.cssText = 'display: block;'
    this._connected = true
    if (this._mount) {
      if (this._hass) this._mount.render(this._hass)
      return
    }
    void import('./lib/cardMount').then(({ mountCard }) => {
      if (!this._connected) return
      this._mount = mountCard(this.shadowRoot!)
      if (this._hass) this._mount.render(this._hass)
    })
  }

  disconnectedCallback() {
    this._connected = false
    this._mount?.unmount()
    this._mount = null
  }
}

if (!customElements.get('homelable-canvas-card')) {
  customElements.define('homelable-canvas-card', HomelableCanvasCard)
}
