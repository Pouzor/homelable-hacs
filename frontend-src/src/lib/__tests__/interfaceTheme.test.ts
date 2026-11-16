import { describe, it, expect } from 'vitest'
import { applyInterfaceTheme, HA_DARK_CLASS, HA_THEME_CLASS } from '@/lib/interfaceTheme'

describe('applyInterfaceTheme', () => {
  it('adds the HA class, and the dark class only in HA dark mode', () => {
    const el = document.createElement('div')
    applyInterfaceTheme(el, 'ha', false)
    expect(el.classList.contains(HA_THEME_CLASS)).toBe(true)
    expect(el.classList.contains(HA_DARK_CLASS)).toBe(false)

    applyInterfaceTheme(el, 'ha', true)
    expect(el.classList.contains(HA_THEME_CLASS)).toBe(true)
    expect(el.classList.contains(HA_DARK_CLASS)).toBe(true)
  })

  it('removes both classes for the original Homelable look', () => {
    const el = document.createElement('div')
    applyInterfaceTheme(el, 'ha', true)
    applyInterfaceTheme(el, 'homelable', true)
    expect(el.classList.contains(HA_THEME_CLASS)).toBe(false)
    expect(el.classList.contains(HA_DARK_CLASS)).toBe(false)
  })

  it('leaves unrelated classes alone', () => {
    const el = document.createElement('div')
    el.className = 'keep-me'
    applyInterfaceTheme(el, 'ha', false)
    applyInterfaceTheme(el, 'homelable', false)
    expect(el.className).toBe('keep-me')
  })
})
