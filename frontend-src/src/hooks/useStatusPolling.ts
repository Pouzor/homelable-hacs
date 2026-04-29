/**
 * HA build: subscribe to status updates from the integration via WS.
 *
 * Pushes updates into `canvasStore.updateNode(...)` to match the original
 * standalone hook's surface. The HA backend currently exposes
 * `homelable/status/get` (poll) — see `subscribeStatus` in api/ha.ts.
 * Real subscription will replace polling once the backend pushes updates.
 */
import { useEffect } from 'react'
import { subscribeStatus, type StatusUpdate } from '@/api/ha'
import { useCanvasStore } from '@/stores/canvasStore'

export function useStatusPolling() {
  const { updateNode } = useCanvasStore()

  useEffect(() => {
    let unsubscribe: (() => void) | null = null
    let cancelled = false

    void subscribeStatus((update: StatusUpdate) => {
      if (cancelled) return
      for (const [nodeId, info] of Object.entries(update)) {
        updateNode(nodeId, {
          status: info.status as 'online' | 'offline' | 'unknown',
          response_time_ms: info.response_time_ms ?? undefined,
        })
      }
    }).then((un) => {
      if (cancelled) un()
      else unsubscribe = un
    })

    return () => {
      cancelled = true
      if (unsubscribe) unsubscribe()
    }
  }, [updateNode])
}
