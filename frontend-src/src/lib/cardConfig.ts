/**
 * Lovelace YAML config for <homelable-canvas-card>.
 *
 * Imported statically by the card entry, so it must stay free of React and of
 * anything that pulls it in (see the note at the top of ha-card.ts).
 *
 * ```yaml
 * type: custom:homelable-canvas-card
 * design_id: <uuid>        # optional, first design when omitted
 * height: 500              # px, default 400
 * title: Network           # optional card header
 * fit_view: true           # default
 * interactive: pan         # pan | none
 * open_on_click: false     # default off — click a node to open http://<ip>
 * ```
 */

export const CARD_TYPE = 'homelable-canvas-card'
export const DEFAULT_HEIGHT = 400
export const INTERACTIVE_MODES = ['pan', 'none'] as const

export type InteractiveMode = (typeof INTERACTIVE_MODES)[number]

export interface HomelableCardConfig {
  design_id?: string
  title?: string
  height: number
  fit_view: boolean
  interactive: InteractiveMode
  open_on_click: boolean
}

/** Thrown message text reaches the user: HA renders it in place of the card. */
function fail(message: string): never {
  throw new Error(`homelable-canvas-card: ${message}`)
}

function optionalString(raw: Record<string, unknown>, key: string): string | undefined {
  const value = raw[key]
  if (value === undefined || value === null) return undefined
  if (typeof value !== 'string' || value.trim() === '') {
    fail(`\`${key}\` must be a non-empty string`)
  }
  return value
}

function boolOr(raw: Record<string, unknown>, key: string, fallback: boolean): boolean {
  const value = raw[key]
  if (value === undefined || value === null) return fallback
  if (typeof value !== 'boolean') fail(`\`${key}\` must be true or false`)
  return value
}

/**
 * Validate a raw Lovelace config into a fully defaulted one.
 * Throws with a readable message on anything unusable — HA shows it on the card.
 */
export function parseCardConfig(raw: unknown): HomelableCardConfig {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    fail('configuration must be a mapping')
  }
  const config = raw as Record<string, unknown>

  let height = DEFAULT_HEIGHT
  const rawHeight = config.height
  if (rawHeight !== undefined && rawHeight !== null) {
    if (typeof rawHeight !== 'number' || !Number.isFinite(rawHeight) || rawHeight <= 0) {
      fail('`height` must be a positive number of pixels')
    }
    height = rawHeight
  }

  let interactive: InteractiveMode = 'pan'
  const rawInteractive = config.interactive
  if (rawInteractive !== undefined && rawInteractive !== null) {
    if (!INTERACTIVE_MODES.includes(rawInteractive as InteractiveMode)) {
      fail(`\`interactive\` must be one of: ${INTERACTIVE_MODES.join(', ')}`)
    }
    interactive = rawInteractive as InteractiveMode
  }

  return {
    design_id: optionalString(config, 'design_id'),
    title: optionalString(config, 'title'),
    height,
    fit_view: boolOr(config, 'fit_view', true),
    interactive,
    open_on_click: boolOr(config, 'open_on_click', false),
  }
}

/** Lovelace asks for a card height in ~50px rows. */
export function cardSize(config: HomelableCardConfig): number {
  return Math.max(1, Math.ceil(config.height / 50))
}
