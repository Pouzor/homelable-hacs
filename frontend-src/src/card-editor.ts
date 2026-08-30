/**
 * Visual editor for <homelable-canvas-card>, registered as
 * <homelable-canvas-card-editor> and returned by the card's getConfigElement().
 *
 * Built on HA's own `<ha-form>`, which the frontend registers globally: no
 * React, so this stays in the eager card entry, and the form matches the
 * built-in cards' editors instead of looking like a transplant. `ha-form` is an
 * internal frontend API rather than a versioned contract — the trade we accept
 * for not reimplementing HA's form stack.
 *
 * Lovelace's contract: setConfig() feeds the current config in, and every edit
 * goes back out as a `config-changed` event carrying the whole config.
 */
import {
  buildSchema,
  computeLabel,
  normalizeConfig,
  toFormValue,
  type DesignOption,
} from './lib/cardEditorForm'
import type { Hass } from './lib/hass'

/** ha-form, as far as we use it. */
interface HaFormElement extends HTMLElement {
  hass?: unknown
  data?: unknown
  schema?: unknown
  computeLabel?: unknown
}

interface DesignsResult {
  designs?: Array<{ id: string; name: string }>
}

/** HA's hass object exposes callWS; our own bridge type only knows connection. */
type EditorHass = Hass & {
  callWS?: <T>(msg: Record<string, unknown>) => Promise<T>
}

class HomelableCanvasCardEditor extends HTMLElement {
  private _hass: EditorHass | null = null
  private _config: Record<string, unknown> = {}
  private _designs: DesignOption[] | null = null
  private _form: HaFormElement | null = null
  private _requestedDesigns = false

  setConfig(config: Record<string, unknown>): void {
    this._config = config ?? {}
    this._render()
  }

  set hass(value: EditorHass) {
    this._hass = value
    if (this._form) this._form.hass = value
    void this._loadDesigns()
    this._render()
  }

  connectedCallback(): void {
    this._render()
  }

  /**
   * Populate the design dropdown. On failure the schema falls back to a
   * free-text field, so a dropped connection doesn't block editing.
   */
  private async _loadDesigns(): Promise<void> {
    if (this._requestedDesigns || !this._hass) return
    this._requestedDesigns = true
    try {
      const message = { type: 'homelable/designs/list' }
      const result = this._hass.callWS
        ? await this._hass.callWS<DesignsResult>(message)
        : await this._hass.connection.sendMessagePromise<DesignsResult>(message)
      this._designs = (result?.designs ?? []).map((d) => ({ id: d.id, name: d.name }))
    } catch {
      this._designs = null
    }
    this._render()
  }

  private _render(): void {
    if (!this.isConnected || !this._hass) return

    if (!this._form) {
      const form = document.createElement('ha-form') as HaFormElement
      form.addEventListener('value-changed', this._onValueChanged)
      this.appendChild(form)
      this._form = form
    }

    this._form.hass = this._hass
    this._form.schema = buildSchema(this._designs)
    this._form.data = toFormValue(this._config)
    this._form.computeLabel = computeLabel
  }

  private _onValueChanged = (event: Event): void => {
    const value = (event as CustomEvent).detail?.value
    if (!value) return
    const config = normalizeConfig(this._config, value)
    this._config = config
    this.dispatchEvent(
      new CustomEvent('config-changed', {
        detail: { config },
        bubbles: true,
        composed: true,
      })
    )
  }

  disconnectedCallback(): void {
    this._form?.removeEventListener('value-changed', this._onValueChanged)
  }
}

export const CARD_EDITOR_TYPE = 'homelable-canvas-card-editor'

if (!customElements.get(CARD_EDITOR_TYPE)) {
  customElements.define(CARD_EDITOR_TYPE, HomelableCanvasCardEditor)
}
