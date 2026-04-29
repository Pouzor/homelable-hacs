/**
 * Bottom handle configuration for multi-handle nodes.
 *
 * Handle IDs:  index 0 = 'bottom' (always the default, backward-compatible)
 *              index 1 = 'bottom-2', index 2 = 'bottom-3', index 3 = 'bottom-4'
 *
 * Invisible target handles follow the same pattern with a '-t' suffix:
 *   'bottom-t', 'bottom-2-t', 'bottom-3-t', 'bottom-4-t'
 */

export const BOTTOM_HANDLE_IDS = ['bottom', 'bottom-2', 'bottom-3', 'bottom-4'] as const

/** Left % position for each handle slot, per count. */
export const BOTTOM_HANDLE_POSITIONS: Record<number, number[]> = {
  1: [50],
  2: [25, 75],
  3: [20, 50, 80],
  4: [15, 38, 62, 85],
}

/**
 * Normalize a raw handle ID coming from a React Flow connection event.
 * Invisible target handles (e.g. 'bottom-2-t') are mapped to their source
 * counterpart ('bottom-2') so the stored edge ID is stable and consistent.
 */
export function normalizeHandle(h: string | null | undefined): string | null {
  if (!h) return null
  if (h === 'top-t') return 'top'
  // 'bottom-t' → 'bottom', 'bottom-2-t' → 'bottom-2', etc.
  const m = h.match(/^(bottom(?:-\d+)?)-t$/)
  if (m) return m[1]
  return h
}

/**
 * Returns the set of handle IDs that are removed when bottom_handles
 * is reduced from `oldCount` to `newCount`.
 */
export function removedBottomHandleIds(oldCount: number, newCount: number): Set<string> {
  const removed = new Set<string>()
  for (let i = newCount; i < oldCount; i++) {
    removed.add(BOTTOM_HANDLE_IDS[i])
  }
  return removed
}
