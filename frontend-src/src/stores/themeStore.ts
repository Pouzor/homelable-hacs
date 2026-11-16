import { create } from 'zustand'
import { resolveThemeId, type ThemeId } from '@/utils/themes'
import type { CustomStyleDef } from '@/types'

/**
 * Look of the app chrome (sidebar, toolbar, dialogs) — independent of the
 * canvas style, which is saved per design.
 * - 'ha': follow the Home Assistant theme (colours, fonts, light / dark)
 * - 'homelable': the original always-dark Homelable look
 */
export type InterfaceTheme = 'ha' | 'homelable'

export const INTERFACE_THEME_STORAGE_KEY = 'homelable.interfaceTheme'

function readInterfaceTheme(): InterfaceTheme {
  try {
    return localStorage.getItem(INTERFACE_THEME_STORAGE_KEY) === 'homelable' ? 'homelable' : 'ha'
  } catch {
    return 'ha'
  }
}

interface ThemeState {
  /** The canvas style as chosen and saved — may be 'ha'. */
  activeTheme: ThemeId
  setTheme: (id: ThemeId) => void
  customStyle: CustomStyleDef
  setCustomStyle: (def: CustomStyleDef) => void
  interfaceTheme: InterfaceTheme
  setInterfaceTheme: (mode: InterfaceTheme) => void
  /** Mirrors `hass.themes.darkMode`; false outside Home Assistant. */
  haDarkMode: boolean
  setHaDarkMode: (dark: boolean) => void
}

export const useThemeStore = create<ThemeState>((set) => ({
  activeTheme: 'default',
  setTheme: (id) => set({ activeTheme: id }),
  customStyle: { nodes: {}, edges: {} },
  setCustomStyle: (def) => set({ customStyle: def }),
  interfaceTheme: readInterfaceTheme(),
  setInterfaceTheme: (mode) => {
    try {
      localStorage.setItem(INTERFACE_THEME_STORAGE_KEY, mode)
    } catch {
      // Storage blocked: the choice still applies for this session.
    }
    set({ interfaceTheme: mode })
  },
  haDarkMode: false,
  setHaDarkMode: (dark) => set((s) => (s.haDarkMode === dark ? s : { haDarkMode: dark })),
}))

/** The preset to draw the canvas with: resolves 'ha' to Light or Dark. */
export const selectCanvasTheme = (s: ThemeState) => resolveThemeId(s.activeTheme, s.haDarkMode)

/** True when the app chrome is rendered light. */
export const selectInterfaceIsLight = (s: ThemeState) => s.interfaceTheme === 'ha' && !s.haDarkMode
