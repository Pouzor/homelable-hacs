/** Shared Zigbee type definitions for the HA panel.
 *
 * Mirrors the standalone repo's frontend/src/components/zigbee/types.ts but
 * drops the MQTT connection request/response — HA's MQTT integration owns
 * the broker config. The panel only needs the parsed networkmap.
 */

export interface ZigbeeNode {
  id: string
  label: string
  type: 'zigbee_coordinator' | 'zigbee_router' | 'zigbee_enddevice'
  ieee_address: string
  friendly_name: string
  device_type: string
  model?: string | null
  vendor?: string | null
  lqi?: number | null
  parent_id?: string | null
}

export interface ZigbeeEdge {
  source: string
  target: string
}

/** Which gateway the mesh is read from. `auto` lets the integration pick:
 *  ZHA when it is set up (no broker, real neighbour tables), else Z2M. */
export type ZigbeeBackend = 'zha' | 'z2m'

export interface ZigbeeNetworkmap {
  nodes: ZigbeeNode[]
  edges: ZigbeeEdge[]
  backend: ZigbeeBackend
  base_topic: string
}

export interface ZigbeeImportResult {
  run_id: string
  status: string
  devices_found: number
  backend: ZigbeeBackend
}

/** Gateways this HA instance can actually serve right now. */
export interface ZigbeeBackends {
  zha: boolean
  z2m: boolean
  default: ZigbeeBackend
}
