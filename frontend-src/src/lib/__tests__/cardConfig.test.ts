import { describe, it, expect } from 'vitest'
import { cardSize, parseCardConfig, DEFAULT_HEIGHT } from '../cardConfig'

describe('parseCardConfig', () => {
  it('fills every default from a bare config', () => {
    expect(parseCardConfig({ type: 'custom:homelable-canvas-card' })).toEqual({
      design_id: undefined,
      title: undefined,
      height: DEFAULT_HEIGHT,
      fit_view: true,
      interactive: 'pan',
      open_on_click: false,
    })
  })

  it('keeps supplied values', () => {
    expect(
      parseCardConfig({
        type: 'custom:homelable-canvas-card',
        design_id: 'abc',
        title: 'Network',
        height: 500,
        fit_view: false,
        interactive: 'none',
        open_on_click: true,
      })
    ).toEqual({
      design_id: 'abc',
      title: 'Network',
      height: 500,
      fit_view: false,
      interactive: 'none',
      open_on_click: true,
    })
  })

  it.each([null, 'a string', ['a', 'list']])('rejects a non-mapping config: %s', (raw) => {
    expect(() => parseCardConfig(raw)).toThrow(/must be a mapping/)
  })

  it.each([0, -10, 'tall', Number.NaN])('rejects height %s', (height) => {
    expect(() => parseCardConfig({ height })).toThrow(/positive number of pixels/)
  })

  it('rejects an unknown interactive mode', () => {
    expect(() => parseCardConfig({ interactive: 'zoom' })).toThrow(/pan, none/)
  })

  it.each(['fit_view', 'open_on_click'])('rejects a non-boolean %s', (key) => {
    expect(() => parseCardConfig({ [key]: 'yes' })).toThrow(/true or false/)
  })

  it.each(['design_id', 'title'])('rejects an empty %s', (key) => {
    expect(() => parseCardConfig({ [key]: '  ' })).toThrow(/non-empty string/)
  })

  it('treats null values as absent, as HA does for cleared YAML keys', () => {
    const config = parseCardConfig({ design_id: null, height: null, fit_view: null })
    expect(config.design_id).toBeUndefined()
    expect(config.height).toBe(DEFAULT_HEIGHT)
    expect(config.fit_view).toBe(true)
  })

  it('prefixes thrown messages with the card type', () => {
    expect(() => parseCardConfig({ height: 0 })).toThrow(/^homelable-canvas-card: /)
  })
})

describe('cardSize', () => {
  it('rounds up to 50px Lovelace rows', () => {
    expect(cardSize(parseCardConfig({ height: 400 }))).toBe(8)
    expect(cardSize(parseCardConfig({ height: 401 }))).toBe(9)
  })

  it('never returns less than one row', () => {
    expect(cardSize(parseCardConfig({ height: 10 }))).toBe(1)
  })
})
