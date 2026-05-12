/**
 * HA WebSocket API adapter.
 *
 * Mirrors the export shape of the original `client.ts` (axios-based) so call
 * sites in components/stores keep working unchanged.
 *
 * REST/CRUD calls that don't have an HA equivalent are stubbed to throw a
 * clear error — these features need WS commands added to the integration.
 */
import { wsCall, wsSubscribe } from '@/lib/hass'

// ─── Wire types ──────────────────────────────────────────────────────────────

interface CanvasPayload {
  nodes: object[]
  edges: object[]
  viewport: object
  custom_style?: object | null
}

// Mimic an axios response so callers using `.data` keep working.
function toAxiosLike<T>(data: T): { data: T } {
  return { data }
}

function notImplemented(name: string): never {
  throw new Error(
    `[homelable-hacs] ${name} is not yet implemented in the HA build. ` +
      `Add a corresponding WS command to custom_components/homelable/websocket.py.`
  )
}

// ─── Canvas ──────────────────────────────────────────────────────────────────

export const canvasApi = {
  load: async () => {
    const canvas = await wsCall<CanvasPayload>('homelable/get_canvas')
    return toAxiosLike(canvas)
  },
  save: async (payload: CanvasPayload) => {
    const result = await wsCall<{ ok: boolean }>('homelable/save_canvas', {
      canvas: payload,
    })
    return toAxiosLike(result)
  },
}

// ─── Scan ────────────────────────────────────────────────────────────────────

export const scanApi = {
  trigger: async () => {
    const result = await wsCall<{
      run_id: string
      status: string
      devices_found: number
      new_devices: number
    }>('homelable/scan/start')
    return toAxiosLike(result)
  },
  pending: async () => {
    const result = await wsCall<{ devices: object[] }>(
      'homelable/scan/pending',
      { status: 'pending' }
    )
    return toAxiosLike(result.devices)
  },
  hidden: async () => {
    const result = await wsCall<{ devices: object[] }>(
      'homelable/scan/pending',
      { status: 'hidden' }
    )
    return toAxiosLike(result.devices)
  },
  approve: async (id: string, nodeData: object) => {
    const result = await wsCall<{
      node: { id: string; type: string; data: object }
      node_id: string
      edges: Array<{ id: string; source: string; target: string }>
      edges_created: number
    }>('homelable/scan/approve', { device_id: id, overrides: nodeData })
    return toAxiosLike(result)
  },
  hide: async (id: string) => {
    const result = await wsCall<{ ok: boolean }>('homelable/scan/hide', {
      device_id: id,
    })
    return toAxiosLike(result)
  },
  stop: async () => {
    const result = await wsCall<{ cancelled: boolean }>(
      'homelable/scan/cancel'
    )
    return toAxiosLike(result)
  },
  ignore: async (id: string) => {
    const result = await wsCall<{ ok: boolean }>('homelable/scan/ignore', {
      device_id: id,
    })
    return toAxiosLike(result)
  },
  restore: async (id: string) => {
    const result = await wsCall<{ ok: boolean }>('homelable/scan/restore', {
      device_id: id,
    })
    return toAxiosLike(result)
  },
  bulkApprove: async (ids: string[], overrides: object = {}) => {
    const result = await wsCall<{
      approved: number
      nodes: object[]
      device_ids: string[]
      node_ids: string[]
      edges: Array<{ id: string; source: string; target: string }>
      edges_created: number
      not_found: string[]
    }>('homelable/scan/approve_batch', { device_ids: ids, overrides })
    return toAxiosLike(result)
  },
  bulkHide: async (ids: string[]) => {
    const result = await wsCall<{ hidden: number }>(
      'homelable/scan/hide_batch',
      { device_ids: ids }
    )
    return toAxiosLike(result)
  },
  bulkRestore: async (ids: string[]) => {
    const result = await wsCall<{ restored: number }>(
      'homelable/scan/restore_batch',
      { device_ids: ids }
    )
    return toAxiosLike(result)
  },
  pendingBySource: async (source: 'scan' | 'zigbee') => {
    const result = await wsCall<{ devices: object[] }>(
      'homelable/scan/pending',
      { status: 'pending', source }
    )
    return toAxiosLike(result.devices)
  },
  runs: async () => {
    const result = await wsCall<{ runs: object[] }>('homelable/scan/runs')
    return toAxiosLike(result.runs)
  },
  clearPending: async () => {
    const result = await wsCall<{ removed: number }>('homelable/scan/clear')
    return toAxiosLike(result)
  },
  getConfig: async () => {
    const result = await wsCall<{ ranges: string[] }>('homelable/scan/get_config')
    return toAxiosLike(result)
  },
}

// ─── Zigbee2MQTT ────────────────────────────────────────────────────────────

import type { ZigbeeNetworkmap, ZigbeeImportResult, ZigbeeNode } from '@/components/zigbee/types'

export const zigbeeApi = {
  /** Fetch the Z2M networkmap. May reject with WS error `mqtt_not_configured`,
   *  `timeout`, or `bad_response`. */
  fetchDevices: async () => {
    const result = await wsCall<ZigbeeNetworkmap>('homelable/zigbee/devices')
    return toAxiosLike(result)
  },
  /** Push selected devices into the pending store (status="pending", source="zigbee"). */
  importDevices: async (devices: ZigbeeNode[]) => {
    const result = await wsCall<ZigbeeImportResult>('homelable/zigbee/import', {
      devices,
    })
    return toAxiosLike(result)
  },
}

// ─── Settings (stubbed: HA owns config via options flow) ────────────────────

export const settingsApi = {
  get: async () =>
    toAxiosLike({ interval_seconds: 60 }),
  save: async (data: { interval_seconds: number }) =>
    toAxiosLike(data),
}

// ─── Nodes/Edges CRUD (handled inline via canvas save in HA build) ──────────

export const nodesApi = {
  create: async (_data: object) => notImplemented('nodesApi.create'),
  update: async (_id: string, _data: object) => notImplemented('nodesApi.update'),
  delete: async (_id: string) => notImplemented('nodesApi.delete'),
}

export const edgesApi = {
  create: async (_data: object) => notImplemented('edgesApi.create'),
  delete: async (_id: string) => notImplemented('edgesApi.delete'),
}

// ─── Auth (HA handles auth — these are no-ops) ──────────────────────────────

export const authApi = {
  login: async (_username: string, _password: string) =>
    notImplemented('authApi.login (HA handles auth)'),
}

// ─── LiveView (not part of HA build) ────────────────────────────────────────

export const liveviewApi = {
  load: async (_key: string) => notImplemented('liveviewApi (standalone-only)'),
}

// ─── Status subscription ────────────────────────────────────────────────────

export interface StatusUpdate {
  [nodeId: string]: { status: string; response_time_ms: number | null }
}

export async function subscribeStatus(
  cb: (update: StatusUpdate) => void
): Promise<() => void> {
  return wsSubscribe<StatusUpdate>('homelable/status/subscribe', cb)
}

// ─── Scan event subscription (progressive scan) ─────────────────────────────

export type ScanEvent =
  | {
      event: 'device_discovered'
      run_id?: string
      device: {
        id?: string
        ip: string
        mac: string | null
        hostname: string | null
        discovery_source?: string | null
      }
    }
  | {
      event: 'device_enriched'
      run_id?: string
      device: {
        id?: string
        ip: string
        mac: string | null
        hostname: string | null
        os: string | null
        open_ports: Array<{ port: number; protocol: string; banner?: string }>
        services: object[]
        suggested_type: string | null
        discovery_source?: string | null
      }
    }
  | { event: 'scan_phase'; run_id?: string; phase: string }
  | { event: 'scan_finished'; run_id?: string; devices_found: number; cancelled?: boolean }
  | { event: 'scan_error'; run_id?: string; error: string }

export async function subscribeScan(
  cb: (event: ScanEvent) => void
): Promise<() => void> {
  return wsSubscribe<ScanEvent>('homelable/scan/subscribe', cb)
}

// Re-export base `api` shim for any direct references; calls here go nowhere.
export const api = {
  get: async (_url: string) =>
    notImplemented(`api.get(${_url}) — refactor to use specific *Api object`),
  post: async (_url: string) =>
    notImplemented(`api.post(${_url}) — refactor to use specific *Api object`),
  patch: async (_url: string) =>
    notImplemented(`api.patch(${_url}) — refactor to use specific *Api object`),
  delete: async (_url: string) =>
    notImplemented(`api.delete(${_url}) — refactor to use specific *Api object`),
}
