import { toast } from 'sonner'
import { useDesignStore } from '@/stores/designStore'
import { useUiStore } from '@/stores/uiStore'
import { useDocsStore } from './store'

/**
 * The ways the rest of the panel reaches into Documentation, and back.
 *
 * The standalone threads these through `App.tsx` as props (homelable #418,
 * #421). Here the Device Inventory lives in the Sidebar rather than in App, so
 * a prop chain would cross three components that never read it; a module both
 * sides import keeps the call sites to one line each.
 */

/**
 * Open the Documentation section on a device's document, writing one from the
 * device's facts when it has none — exactly as picking the device in the
 * section's own tree would.
 */
export async function openDocumentationForDevice(deviceId: string, label: string): Promise<void> {
  useUiStore.getState().setView('documentation')
  const ok = await useDocsStore.getState().openForDevice(deviceId, label)
  if (!ok) toast.error('Could not open the documentation for that device')
}

/**
 * The Sidebar's way in: the section, landing on the homepage when there is one
 * — unless the open document has unsaved edits, which `openHome` never swaps
 * out (homelable #583).
 */
export async function openDocumentation(): Promise<void> {
  useUiStore.getState().setView('documentation')
  await useDocsStore.getState().openHome()
}

/** Open one document by id, e.g. from a search hit. */
export async function openDocument(docId: string): Promise<void> {
  useUiStore.getState().setView('documentation')
  await useDocsStore.getState().open(docId)
}

/**
 * The design a `?design=<id>` link names, or null.
 *
 * The homepage template lists every canvas that way. In the standalone the
 * link reloads the SPA on that design; inside Home Assistant it would navigate
 * the HA frontend away from the panel, so the link is caught and turned into a
 * design switch instead.
 */
export function designIdFromHref(href: string | undefined): string | null {
  if (!href || !href.startsWith('?')) return null
  return new URLSearchParams(href).get('design')
}

/** Leave Documentation for the canvas of one design. */
export function openCanvas(designId: string): void {
  const designs = useDesignStore.getState()
  if (designs.designs.some((d) => d.id === designId)) designs.setActiveDesign(designId)
  useUiStore.getState().setView('canvas')
}
