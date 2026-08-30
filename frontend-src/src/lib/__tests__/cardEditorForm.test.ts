import { describe, it, expect } from 'vitest'
import { buildSchema, computeLabel, normalizeConfig, toFormValue } from '../cardEditorForm'

const BASE = { type: 'custom:homelable-canvas-card' }

const field = (designs: Parameters<typeof buildSchema>[0], name: string) =>
  buildSchema(designs).find((entry) => entry.name === name)!

describe('buildSchema', () => {
  it('offers the designs as a dropdown', () => {
    const design = field([{ id: 'a', name: 'Home' }, { id: 'b', name: 'Rack' }], 'design_id')
    expect(design.selector.select).toEqual({
      mode: 'dropdown',
      options: [
        { value: 'a', label: 'Home' },
        { value: 'b', label: 'Rack' },
      ],
    })
  })

  it.each([null, []])(
    'falls back to a text field when the design list is unavailable: %s',
    (designs) => {
      const design = field(designs, 'design_id')
      expect(design.selector.text).toEqual({})
      expect(design.selector.select).toBeUndefined()
    }
  )

  it('exposes the two interaction modes', () => {
    const options = (field(null, 'interactive').selector.select as { options: unknown[] }).options
    expect(options).toHaveLength(2)
    expect(options[0]).toEqual({ value: 'pan', label: 'Pan and zoom' })
  })

  it('bounds the height', () => {
    expect(field(null, 'height').selector.number).toMatchObject({ min: 100, max: 2000 })
  })
})

describe('computeLabel', () => {
  it('names the fields', () => {
    expect(computeLabel({ name: 'design_id' })).toBe('Design')
  })

  it('falls back to the raw key for anything unlabelled', () => {
    expect(computeLabel({ name: 'mystery' })).toBe('mystery')
  })
})

describe('toFormValue', () => {
  it('shows the effective values for an otherwise empty config', () => {
    expect(toFormValue(BASE)).toEqual({
      design_id: '',
      title: '',
      height: 400,
      fit_view: true,
      interactive: 'pan',
      open_on_click: false,
    })
  })

  it('shows what the config actually holds', () => {
    expect(toFormValue({ ...BASE, height: 700, interactive: 'none' })).toMatchObject({
      height: 700,
      interactive: 'none',
    })
  })
})

describe('normalizeConfig', () => {
  it('drops every field left at its default', () => {
    const config = normalizeConfig(BASE, {
      design_id: '',
      title: '',
      height: 400,
      fit_view: true,
      interactive: 'pan',
      open_on_click: false,
    })
    expect(config).toEqual(BASE)
  })

  it('keeps the values that differ from the defaults', () => {
    const config = normalizeConfig(BASE, {
      design_id: 'abc',
      title: 'Network',
      height: 700,
      fit_view: false,
      interactive: 'none',
      open_on_click: true,
    })
    expect(config).toEqual({
      ...BASE,
      design_id: 'abc',
      title: 'Network',
      height: 700,
      fit_view: false,
      interactive: 'none',
      open_on_click: true,
    })
  })

  it('carries the dashboard layout through, instead of resetting the card size', () => {
    const previous = { ...BASE, grid_options: { columns: 'full', rows: 'auto' }, view_layout: {} }
    const config = normalizeConfig(previous, toFormValue(previous))
    expect(config.grid_options).toEqual({ columns: 'full', rows: 'auto' })
    expect(config.view_layout).toEqual({})
    expect(config.type).toBe(BASE.type)
  })

  it('clears a field the user emptied', () => {
    const previous = { ...BASE, title: 'Old', design_id: 'abc' }
    const config = normalizeConfig(previous, { ...toFormValue(previous), title: '   ' })
    expect(config).not.toHaveProperty('title')
    expect(config.design_id).toBe('abc')
  })

  it('trims whitespace around the values it keeps', () => {
    const config = normalizeConfig(BASE, { title: '  Network  ', design_id: ' abc ' })
    expect(config.title).toBe('Network')
    expect(config.design_id).toBe('abc')
  })

  it('ignores a height that is not a usable number', () => {
    expect(normalizeConfig(BASE, { height: Number.NaN })).not.toHaveProperty('height')
  })

  it('produces a config the card itself accepts', async () => {
    const { parseCardConfig } = await import('../cardConfig')
    const config = normalizeConfig(BASE, {
      design_id: 'abc',
      height: 700,
      interactive: 'none',
      fit_view: false,
      open_on_click: true,
      title: 'Network',
    })
    expect(() => parseCardConfig(config)).not.toThrow()
  })
})
