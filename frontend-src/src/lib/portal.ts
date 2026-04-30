/**
 * Shadow DOM portal target.
 *
 * HA mounts our panel inside a custom element with its own shadow root. Radix
 * / Base UI portals default to `document.body`, which is *outside* the shadow
 * boundary — portaled content (dialogs, selects) ends up unstyled because our
 * CSS lives only in the shadow root.
 *
 * `ShadowRootContext` carries the element we want portals to mount into. The
 * panel provides it once at mount time; UI primitives read it via
 * `useShadowContainer()` and pass it as the `container` prop on their Portal.
 */
import { createContext, useContext } from 'react'

export const ShadowRootContext = createContext<HTMLElement | null>(null)

export function useShadowContainer(): HTMLElement | undefined {
  return useContext(ShadowRootContext) ?? undefined
}
