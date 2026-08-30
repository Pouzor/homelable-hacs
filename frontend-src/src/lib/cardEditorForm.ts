/**
 * Schema and config plumbing for the card's visual editor, kept apart from the
 * element so it can be tested without a DOM or HA's `<ha-form>`.
 *
 * React-free, like the rest of the eager card entry.
 */
import { DEFAULT_HEIGHT, INTERACTIVE_MODES } from './cardConfig'

export interface DesignOption {
  id: string
  name: string
}

/** The subset of ha-form's schema shape we produce. */
export interface FormSchemaEntry {
  name: string
  selector: Record<string, unknown>
}

/** What ha-form round-trips: every field, defaults included. */
export interface FormValue {
  design_id?: string
  title?: string
  height?: number
  fit_view?: boolean
  interactive?: string
  open_on_click?: boolean
}

const LABELS: Record<string, string> = {
  design_id: 'Design',
  title: 'Title',
  height: 'Height (px)',
  fit_view: 'Fit the canvas on load',
  interactive: 'Interaction',
  open_on_click: 'Open http://<ip> when a node is clicked',
}

export function computeLabel(schema: { name: string }): string {
  return LABELS[schema.name] ?? schema.name
}

/**
 * `designs` is null while the list is still loading or after the WS call
 * failed: the design then gets a free-text field, so a broken connection
 * doesn't leave the editor unusable.
 */
export function buildSchema(designs: DesignOption[] | null): FormSchemaEntry[] {
  const design: FormSchemaEntry =
    designs && designs.length > 0
      ? {
          name: 'design_id',
          selector: {
            select: {
              mode: 'dropdown',
              options: designs.map((d) => ({ value: d.id, label: d.name })),
            },
          },
        }
      : { name: 'design_id', selector: { text: {} } }

  return [
    design,
    { name: 'title', selector: { text: {} } },
    {
      name: 'height',
      selector: {
        number: { min: 100, max: 2000, step: 10, mode: 'box', unit_of_measurement: 'px' },
      },
    },
    { name: 'fit_view', selector: { boolean: {} } },
    {
      name: 'interactive',
      selector: {
        select: {
          mode: 'dropdown',
          options: INTERACTIVE_MODES.map((mode) => ({
            value: mode,
            label: mode === 'pan' ? 'Pan and zoom' : 'Locked',
          })),
        },
      },
    },
    { name: 'open_on_click', selector: { boolean: {} } },
  ]
}

/** Fill the form from a stored config, so ha-form shows the effective values. */
export function toFormValue(config: Record<string, unknown>): FormValue {
  return {
    design_id: (config.design_id as string) ?? '',
    title: (config.title as string) ?? '',
    height: (config.height as number) ?? DEFAULT_HEIGHT,
    fit_view: (config.fit_view as boolean) ?? true,
    interactive: (config.interactive as string) ?? 'pan',
    open_on_click: (config.open_on_click as boolean) ?? false,
  }
}

/**
 * Merge an edit back into the stored config.
 *
 * Keys left at their default are dropped, so the YAML stays as short as what a
 * user would have written. Everything we don't own is carried through
 * untouched — `grid_options` (the dashboard's own layout) lives in this same
 * config, and dropping it would reset the card's size on every edit.
 */
export function normalizeConfig(
  previous: Record<string, unknown>,
  value: FormValue
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...previous }

  const setOrDelete = (key: string, keep: boolean, resolved: unknown) => {
    if (keep) next[key] = resolved
    else delete next[key]
  }

  const design = value.design_id?.trim() ?? ''
  setOrDelete('design_id', design !== '', design)

  const title = value.title?.trim() ?? ''
  setOrDelete('title', title !== '', title)

  const height = value.height
  setOrDelete(
    'height',
    typeof height === 'number' && Number.isFinite(height) && height !== DEFAULT_HEIGHT,
    height
  )

  setOrDelete('fit_view', value.fit_view === false, false)
  setOrDelete('interactive', value.interactive === 'none', 'none')
  setOrDelete('open_on_click', value.open_on_click === true, true)

  return next
}
