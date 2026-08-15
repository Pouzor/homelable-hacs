/**
 * HA WebSocket API adapter.
 *
 * Mirrors the export shape of the original `client.ts` (axios-based) so call
 * sites in components/stores keep working unchanged.
 *
 * REST/CRUD calls that don't have an HA equivalent are stubbed to throw a
 * clear error — these features need WS commands added to the integration.
 */
import { wsCall, wsSubscribe, getHass } from '@/lib/hass'

// ─── Wire types ──────────────────────────────────────────────────────────────

interface CanvasPayload {
  nodes: object[]
  edges: object[]
  viewport: object
  custom_style?: object | null
  design_id?: string | null
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
  load: async (design_id?: string) => {
    const canvas = await wsCall<CanvasPayload>('homelable/get_canvas', {
      design_id: design_id ?? null,
    })
    return toAxiosLike(canvas)
  },
  save: async (payload: CanvasPayload) => {
    // design_id travels as a top-level WS param; the canvas blob itself stays
    // free of it so stored shapes match the standalone serializer.
    const { design_id = null, ...canvas } = payload
    const result = await wsCall<{ ok: boolean }>('homelable/save_canvas', {
      canvas,
      design_id,
    })
    return toAxiosLike(result)
  },
}

// ─── Designs (multiple canvases) ─────────────────────────────────────────────

import type { Design } from '@/types'

export const designsApi = {
  list: async () => {
    const result = await wsCall<{ designs: Design[] }>('homelable/designs/list')
    return toAxiosLike(result.designs)
  },
  create: async (data: { name: string; icon?: string; design_type?: string }) => {
    const result = await wsCall<Design>('homelable/designs/create', data)
    return toAxiosLike(result)
  },
  copy: async (sourceId: string, data: { name: string; icon?: string }) => {
    const result = await wsCall<Design>('homelable/designs/copy', {
      source_id: sourceId,
      ...data,
    })
    return toAxiosLike(result)
  },
  update: async (id: string, data: { name?: string; icon?: string }) => {
    const result = await wsCall<Design>('homelable/designs/update', {
      design_id: id,
      ...data,
    })
    return toAxiosLike(result)
  },
  delete: async (id: string) => {
    const result = await wsCall<{ ok: boolean }>('homelable/designs/delete', {
      design_id: id,
    })
    return toAxiosLike(result)
  },
}

// ─── Media (floor-plan images) ────────────────────────────────────────────────

export const mediaApi = {
  /**
   * Upload an image over HTTP (multipart) — too large for the 4 MB WS frame
   * limit. Authenticated with the HA access token; returns the served URL
   * (e.g. /homelable_media/<uuid>.png).
   */
  upload: async (file: File): Promise<{ url: string; filename: string }> => {
    const hass = getHass()
    const token = hass.auth?.accessToken ?? hass.auth?.data?.access_token
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/homelable/media/upload', {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    })
    if (!res.ok) throw new Error(`media upload failed: ${res.status}`)
    return res.json()
  },
  delete: async (filename: string): Promise<void> => {
    await wsCall('homelable/media/delete', { filename })
  },
}

// ─── Scan ────────────────────────────────────────────────────────────────────

export interface DeepScanConfig {
  http_ranges: string[]
  http_probe_enabled: boolean
  verify_tls: boolean
}

// A device bulk-approve refused to place because an equivalent node already
// exists on the target design (same ip/mac/ieee). `existing_node_id` points at
// the node already there so the UI can link to it.
export interface SkippedDevice {
  device_id: string
  label: string
  match: 'ip' | 'mac' | 'ieee'
  value: string
  existing_node_id: string | null
}

// Conflict body returned by single approve when a same-design duplicate exists.
export interface DuplicateNodeConflict {
  duplicate: true
  existing_node_id: string
  existing_label: string | null
  match: 'ip' | 'mac' | 'ieee'
  value: string
}

export const scanApi = {
  /** Start a scan. Optional deep-scan options are a per-scan override (extra
   *  port ranges + HTTP probe); they are not persisted. */
  trigger: async (deepScan?: Partial<DeepScanConfig>) => {
    const result = await wsCall<{
      run_id: string
      status: string
      devices_found: number
      new_devices: number
    }>('homelable/scan/start', deepScan ?? {})
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
  approve: async (id: string, nodeData: object, designId?: string | null) => {
    // Success either places the node OR (same-design duplicate) returns a
    // `duplicate` conflict so the caller can ask the user. `node`/`node_id` are
    // absent in the duplicate case.
    const result = await wsCall<{
      node?: { id: string; type: string; data: object }
      node_id?: string
      edges?: Array<{
        id: string
        source: string
        target: string
        type?: string
        sourceHandle?: string | null
        targetHandle?: string | null
      }>
      edges_created?: number
      duplicate?: DuplicateNodeConflict
    }>('homelable/scan/approve', { device_id: id, overrides: nodeData, design_id: designId ?? null })
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
  bulkApprove: async (ids: string[], overrides: object = {}, designId?: string | null) => {
    const result = await wsCall<{
      approved: number
      nodes: object[]
      device_ids: string[]
      node_ids: string[]
      edges: Array<{
        id: string
        source: string
        target: string
        type?: string
        sourceHandle?: string | null
        targetHandle?: string | null
      }>
      edges_created: number
      skipped: string[]
      skipped_devices: SkippedDevice[]
      not_found: string[]
    }>('homelable/scan/approve_batch', { device_ids: ids, overrides, design_id: designId ?? null })
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
  pendingBySource: async (source: 'scan' | 'zigbee' | 'zwave') => {
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

// ─── Zigbee (Zigbee2MQTT / ZHA) ─────────────────────────────────────────────

import type {
  ZigbeeBackend,
  ZigbeeBackends,
  ZigbeeNetworkmap,
  ZigbeeImportResult,
} from '@/components/zigbee/types'

/** Omitted backend = let the integration choose (ZHA when set up, else Z2M). */
type BackendArg = ZigbeeBackend | 'auto' | undefined

// The WS command schema rejects an explicit `backend: null`, so omit the key
// entirely rather than sending an empty one.
const backendArgs = (backend: BackendArg) => (backend ? { backend } : {})

export const zigbeeApi = {
  /** Which Zigbee gateways this HA instance can serve. */
  backends: async () => {
    const result = await wsCall<ZigbeeBackends>('homelable/zigbee/backends')
    return toAxiosLike(result)
  },
  /** Fetch the Zigbee mesh. May reject with WS error `zha_not_configured`,
   *  `mqtt_not_configured`, `timeout`, or `bad_response`. */
  fetchDevices: async (backend?: BackendArg) => {
    const result = await wsCall<ZigbeeNetworkmap>(
      'homelable/zigbee/devices',
      backendArgs(backend)
    )
    return toAxiosLike(result)
  },
  /** Kick off a background Zigbee import (fetch mesh + push all discovered
   *  devices into the pending store). Returns a running scan run;
   *  progress/completion is polled via Scan History. */
  startImport: async (backend?: BackendArg) => {
    const result = await wsCall<ZigbeeImportResult>(
      'homelable/zigbee/import',
      backendArgs(backend)
    )
    return toAxiosLike(result)
  },
}

// ─── Z-Wave JS UI ───────────────────────────────────────────────────────────

import type { ZwaveNetwork, ZwaveImportResult } from '@/components/zwave/types'

export const zwaveApi = {
  /** Fetch the Z-Wave node list. May reject with WS error `mqtt_not_configured`,
   *  `timeout`, or `bad_response`. */
  fetchDevices: async () => {
    const result = await wsCall<ZwaveNetwork>('homelable/zwave/devices')
    return toAxiosLike(result)
  },
  /** Kick off a background Z-Wave import (fetch node list + push all discovered
   *  devices into the pending store). Returns a running scan run;
   *  progress/completion is polled via Scan History. */
  startImport: async () => {
    const result = await wsCall<ZwaveImportResult>('homelable/zwave/import')
    return toAxiosLike(result)
  },
}

// ─── Proxmox VE ─────────────────────────────────────────────────────────────

import type {
  ProxmoxEdge,
  ProxmoxNode,
} from '@/components/proxmox/types'

/** Connection params. A blank token falls back to the token stored in the HA
 *  integration options — the panel never has to hold the secret. */
export interface ProxmoxConnection {
  host: string
  port: number
  token_id?: string
  token_secret?: string
  verify_tls: boolean
}

export interface ProxmoxImportNetworkResult {
  nodes: ProxmoxNode[]
  edges: ProxmoxEdge[]
  cluster_pairs: [string, string][]
  device_count: number
  advisory: string | null
}

export interface ProxmoxConfig {
  host: string
  port: number
  verify_tls: boolean
  sync_enabled: boolean
  sync_interval: number
  token_configured: boolean
}

export const proxmoxApi = {
  /** Non-secret config (host/port/tls/sync + whether a token is configured). */
  getConfig: async () => {
    const result = await wsCall<ProxmoxConfig>('homelable/proxmox/get_config')
    return toAxiosLike(result)
  },
  /** Reachability + auth probe. May reject with WS error `not_configured`. */
  testConnection: async (payload: ProxmoxConnection) => {
    const result = await wsCall<{ connected: boolean; message: string }>(
      'homelable/proxmox/test_connection',
      payload as unknown as Record<string, unknown>,
    )
    return toAxiosLike(result)
  },
  /** Fetch the inventory for a direct canvas drop (nodes + edges + clusters). */
  importNetwork: async (payload: ProxmoxConnection) => {
    const result = await wsCall<ProxmoxImportNetworkResult>(
      'homelable/proxmox/import',
      payload as unknown as Record<string, unknown>,
    )
    return toAxiosLike(result)
  },
  /** Kick off a background import into pending; poll Scan History for progress. */
  importToPending: async (payload: ProxmoxConnection) => {
    const result = await wsCall<{ run_id: string; status: string; devices_found: number }>(
      'homelable/proxmox/import_pending',
      payload as unknown as Record<string, unknown>,
    )
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

// ─── Per-service status subscription ────────────────────────────────────────

export interface ServiceStatusUpdate {
  node_id: string
  services: Array<{ port?: number; protocol?: string; status: 'online' | 'offline' | 'unknown' }>
  checked_at?: string
}

export async function subscribeServiceStatus(
  cb: (update: ServiceStatusUpdate) => void
): Promise<() => void> {
  return wsSubscribe<ServiceStatusUpdate>('homelable/service_status/subscribe', cb)
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
