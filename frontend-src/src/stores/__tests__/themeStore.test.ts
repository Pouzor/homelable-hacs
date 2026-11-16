import { describe, it, expect, beforeEach } from 'vitest'
import { INTERFACE_THEME_STORAGE_KEY, selectCanvasTheme, selectInterfaceIsLight, useThemeStore } from '@/stores/themeStore'
import type { CustomStyleDef } from '@/types'

describe('themeStore', () => {
  beforeEach(() => {
    useThemeStore.setState({ activeTheme: 'default', customStyle: { nodes: {}, edges: {} } })
  })

  it('starts with default theme', () => {
    expect(useThemeStore.getState().activeTheme).toBe('default')
  })

  it('setTheme updates activeTheme', () => {
    useThemeStore.getState().setTheme('matrix')
    expect(useThemeStore.getState().activeTheme).toBe('matrix')
  })

  it('setTheme can switch between all presets including custom', () => {
    const themes = ['default', 'dark', 'light', 'neon', 'matrix', 'custom'] as const
    for (const id of themes) {
      useThemeStore.getState().setTheme(id)
      expect(useThemeStore.getState().activeTheme).toBe(id)
    }
  })

  it('setTheme back to default after neon', () => {
    useThemeStore.getState().setTheme('neon')
    useThemeStore.getState().setTheme('default')
    expect(useThemeStore.getState().activeTheme).toBe('default')
  })

  it('starts with empty customStyle', () => {
    const { customStyle } = useThemeStore.getState()
    expect(customStyle.nodes).toEqual({})
    expect(customStyle.edges).toEqual({})
  })

  it('setCustomStyle replaces the entire definition', () => {
    const def: CustomStyleDef = {
      nodes: { server: { borderColor: '#ff0000', borderOpacity: 1, bgColor: '#000000', bgOpacity: 1, iconColor: '#ff0000', iconOpacity: 1, width: 200, height: 80 } },
      edges: { ethernet: { color: '#00ff00', opacity: 0.8, pathStyle: 'bezier', animated: 'none', arrowStart: 'none', arrowEnd: 'none' } },
    }
    useThemeStore.getState().setCustomStyle(def)
    expect(useThemeStore.getState().customStyle.nodes.server?.borderColor).toBe('#ff0000')
    expect(useThemeStore.getState().customStyle.edges.ethernet?.color).toBe('#00ff00')
  })

  it('setCustomStyle with empty def clears styles', () => {
    useThemeStore.getState().setCustomStyle({ nodes: { server: { borderColor: '#aaa', borderOpacity: 1, bgColor: '#000', bgOpacity: 1, iconColor: '#aaa', iconOpacity: 1, width: 0, height: 0 } }, edges: {} })
    useThemeStore.getState().setCustomStyle({ nodes: {}, edges: {} })
    expect(useThemeStore.getState().customStyle.nodes).toEqual({})
  })
})

describe('themeStore — Home Assistant theme', () => {
  beforeEach(() => {
    localStorage.clear()
    useThemeStore.setState({ activeTheme: 'default', interfaceTheme: 'ha', haDarkMode: false })
  })

  it("resolves the 'ha' canvas style to Light or Dark from HA's mode", () => {
    useThemeStore.getState().setTheme('ha')
    expect(selectCanvasTheme(useThemeStore.getState())).toBe('light')
    useThemeStore.getState().setHaDarkMode(true)
    expect(selectCanvasTheme(useThemeStore.getState())).toBe('dark')
    // The saved choice stays 'ha' so it keeps following HA.
    expect(useThemeStore.getState().activeTheme).toBe('ha')
  })

  it('passes every other canvas style through unchanged', () => {
    useThemeStore.getState().setHaDarkMode(true)
    useThemeStore.getState().setTheme('neon')
    expect(selectCanvasTheme(useThemeStore.getState())).toBe('neon')
  })

  it('persists the interface choice', () => {
    useThemeStore.getState().setInterfaceTheme('homelable')
    expect(useThemeStore.getState().interfaceTheme).toBe('homelable')
    expect(localStorage.getItem(INTERFACE_THEME_STORAGE_KEY)).toBe('homelable')
  })

  it('renders the interface light only in HA light mode', () => {
    expect(selectInterfaceIsLight(useThemeStore.getState())).toBe(true)
    useThemeStore.getState().setHaDarkMode(true)
    expect(selectInterfaceIsLight(useThemeStore.getState())).toBe(false)
    useThemeStore.getState().setHaDarkMode(false)
    useThemeStore.getState().setInterfaceTheme('homelable')
    expect(selectInterfaceIsLight(useThemeStore.getState())).toBe(false)
  })
})
