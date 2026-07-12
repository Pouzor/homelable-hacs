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

export interface ZigbeeNetworkmap {
  nodes: ZigbeeNode[]
  edges: ZigbeeEdge[]
  base_topic: string
}

export interface ZigbeeImportResult {
  run_id: string
  status: string
  devices_found: number
}
