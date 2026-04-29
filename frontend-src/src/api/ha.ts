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
    const result = await wsCall<{ node: object }>(
      'homelable/scan/approve',
      { device_id: id, overrides: nodeData }
    )
    return toAxiosLike(result.node)
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
  // Not yet implemented — stubs throw on call so missing features surface fast.
  ignore: async (_id: string) => notImplemented('scanApi.ignore'),
  bulkApprove: async (_ids: string[]) => notImplemented('scanApi.bulkApprove'),
  bulkHide: async (_ids: string[]) => notImplemented('scanApi.bulkHide'),
  runs: async () => notImplemented('scanApi.runs'),
  clearPending: async () => notImplemented('scanApi.clearPending'),
  getConfig: async () => notImplemented('scanApi.getConfig'),
  saveConfig: async (_data: { ranges: string[] }) =>
    notImplemented('scanApi.saveConfig'),
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
  // Backend currently only exposes a get; subscription added with status push.
  // Until then, this is a no-op subscription that polls every 30s as fallback.
  let stopped = false
  const tick = async () => {
    if (stopped) return
    try {
      const data = await wsCall<StatusUpdate>('homelable/status/get')
      cb(data || {})
    } catch {
      /* ignore */
    }
  }
  void tick()
  const handle = window.setInterval(tick, 30000)
  return () => {
    stopped = true
    window.clearInterval(handle)
  }
  // TODO: replace with real subscription once backend exposes it:
  // return wsSubscribe<StatusUpdate>('homelable/status/subscribe', cb)
  void wsSubscribe // silence unused import lint
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
