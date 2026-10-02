/**
 * Build entry for the Lovelace card — registers <homelable-canvas-card>.
 *
 * HA loads this bundle as a module on *every* frontend page (panel.py calls
 * frontend.add_extra_js_url), so it stays deliberately tiny: no React, no
 * Tailwind, no canvas. Everything heavy lives behind the dynamic import in
 * `connectedCallback`, which only runs once a card is actually on a dashboard.
 * Keep it that way — a static import of anything under `lib/` other than the
 * config module, the editor and the types below would pull React back into
 * every page load.
 */
import { CARD_EDITOR_TYPE } from './card-editor'
import { CARD_TYPE, cardSize, parseCardConfig, type HomelableCardConfig } from './lib/cardConfig'
// `@i18n/core`, not `@i18n` — the barrel pulls in React via useSyncExternalStore,
// and this bundle is loaded on every HA page. The picker entry is built once
// here, so it reads whatever locale is current at load time.
import { getLocale, subscribe, syncLocaleFromHass, t } from './i18n/core'
import type { Hass } from './lib/hass'
import type { CardMount } from './lib/cardMount'

/**
 * Connected cards, in insertion order. Node components read the *global*
 * canvasStore singleton, so two canvases on one page would fight over it —
 * only the first card renders one, the rest show a notice. Lifting that limit
 * means moving the store behind a context (TODO-002 v2).
 */
const connected: HomelableCanvasCard[] = []

function refreshInstances(): void {
  for (const card of connected) card.renderCard()
}

class HomelableCanvasCard extends HTMLElement {
  private _hass: Hass | null = null
  private _config: HomelableCardConfig | null = null
  private _mount: CardMount | null = null
  private _language: string | undefined

  constructor() {
    super()
    this.attachShadow({ mode: 'open' })
  }

  /** Lovelace calls this on every config edit; it may throw to reject the YAML. */
  setConfig(config: unknown): void {
    this._config = parseCardConfig(config)
    this.renderCard()
  }

  set hass(value: Hass) {
    this._hass = value
    // HA reassigns `hass` many times a second; only re-derive on a real change.
    if (value.language !== this._language) {
      this._language = value.language
      syncLocaleFromHass(value.language)
    }
    this.renderCard()
  }

  get hass(): Hass | null {
    return this._hass
  }

  getCardSize(): number {
    return this._config ? cardSize(this._config) : 8
  }

  static getStubConfig(): Record<string, unknown> {
    return { type: `custom:${CARD_TYPE}` }
  }

  /**
   * Lovelace calls this synchronously and expects a ready element, which is why
   * the editor is imported statically. It is React-free — it renders HA's own
   * <ha-form> — so it costs nothing on pages that never open it.
   */
  static getConfigElement(): HTMLElement {
    return document.createElement(CARD_EDITOR_TYPE)
  }

  connectedCallback(): void {
    this.style.cssText = 'display: block;'
    if (!connected.includes(this)) connected.push(this)

    if (this._mount) {
      // A card that is only being moved keeps its React root; primary status
      // may have changed while it was detached.
      refreshInstances()
      return
    }
    void import('./lib/cardMount').then(({ mountCard }) => {
      if (!connected.includes(this)) return
      this._mount = mountCard(this.shadowRoot!)
      refreshInstances()
    })
  }

  disconnectedCallback(): void {
    const index = connected.indexOf(this)
    if (index !== -1) connected.splice(index, 1)
    this._mount?.unmount()
    this._mount = null
    // Removing the primary card promotes the next one.
    refreshInstances()
  }

  /** Re-render with the current hass, config and primary status. */
  renderCard(): void {
    if (!this._mount || !this._hass || !this._config) return
    this._mount.render(this._hass, this._config, {
      primary: connected[0] === this,
    })
  }
}

if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, HomelableCanvasCard)

  // Card picker entry. `preview: false` — the picker renders cards without a
  // hass connection, and ours has nothing to show without one.
  const w = window as unknown as {
    customCards?: Array<Record<string, unknown>>
  }
  w.customCards = w.customCards ?? []
  const entry: Record<string, unknown> = {
    type: CARD_TYPE,
    name: t('Homelable Canvas'),
    description: t('Read-only view of a Homelable network canvas.'),
    preview: false,
    documentationURL: 'https://github.com/Pouzor/homelable-hacs',
  }
  w.customCards.push(entry)

  // This module runs on every HA page, long before a `hass` object exists, so
  // the locale is still the English default when `entry` is built. HA reads
  // `customCards` fresh each time the picker opens, so refreshing the same
  // object in place is enough — no re-registration and no second entry.
  subscribe(() => {
    if (getLocale() === 'en') return
    entry.name = t('Homelable Canvas')
    entry.description = t('Read-only view of a Homelable network canvas.')
  })
}
