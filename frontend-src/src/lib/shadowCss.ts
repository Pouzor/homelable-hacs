/**
 * Shadow-root style injection, shared by every custom element we register
 * (the panel and the Lovelace card).
 *
 * HA mounts our elements inside its own shadow roots, so `document.head`
 * stylesheets never reach us. CSS comes from two places:
 *   - index.css / App.css via `?inline` (Tailwind v4 + theme vars; Tailwind's
 *     Vite plugin emits CSS through a path the inject-by-js plugin doesn't see)
 *   - CSS modules via `window.__HOMELABLE_CSS__`, stashed by
 *     cssInjectedByJsPlugin (see vite.config.ts). Both the panel and the card
 *     bundle append to that global, so whichever loads first — HA loads the
 *     card module on every page — the other still finds its own CSS there.
 * `:root` is rewritten to `:host` so theme custom properties land on the shadow
 * host instead of `<html>` (which lives outside the boundary).
 */
import indexCss from '@/index.css?inline'
import appCss from '@/App.css?inline'

declare global {
  interface Window {
    __HOMELABLE_CSS__?: string
  }
}

/** Idempotent: a second call on the same root is a no-op. */
export function injectShadowStyles(shadow: ShadowRoot): void {
  if (shadow.querySelector('style[data-homelable]')) return
  const style = document.createElement('style')
  style.setAttribute('data-homelable', '')
  const combined = `${indexCss}\n${appCss}\n${window.__HOMELABLE_CSS__ ?? ''}`
  style.textContent = combined.replace(/:root\b/g, ':host')
  shadow.appendChild(style)
}
