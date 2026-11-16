/**
 * Applies the interface theme to the panel's mount point.
 *
 * The classes go on the mount point rather than the shadow host because every
 * dialog / select / toast portals into that element (see `lib/portal.ts`), so
 * one class reaches all of them. `index.css` keys the HA token mapping off
 * `.homelable-ha`; without it the original Homelable dark tokens apply.
 */
import type { InterfaceTheme } from '@/stores/themeStore'

export const HA_THEME_CLASS = 'homelable-ha'
export const HA_DARK_CLASS = 'homelable-ha-dark'

export function applyInterfaceTheme(el: HTMLElement, mode: InterfaceTheme, haDarkMode: boolean): void {
  const ha = mode === 'ha'
  el.classList.toggle(HA_THEME_CLASS, ha)
  el.classList.toggle(HA_DARK_CLASS, ha && haDarkMode)
}
