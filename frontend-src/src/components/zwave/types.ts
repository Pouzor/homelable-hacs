/** Shared Z-Wave type definitions for the HA panel.
 *
 * Mirrors the standalone repo's frontend/src/components/zwave/types.ts but
 * drops the MQTT connection request/response — HA's MQTT integration owns
 * the broker config. The panel only needs the parsed node list.
 */

export interface ZwaveNode {
  id: string
  label: string
  type: 'zwave_coordinator' | 'zwave_router' | 'zwave_enddevice'
  ieee_address: string
  friendly_name: string
  device_type: string
  model?: string | null
  vendor?: string | null
  lqi?: number | null
  parent_id?: string | null
}

export interface ZwaveEdge {
  source: string
  target: string
}

export interface ZwaveNetwork {
  nodes: ZwaveNode[]
  edges: ZwaveEdge[]
  prefix: string
  gateway: string
}

export interface ZwaveImportResult {
  run_id: string
  status: string
  devices_found: number
}
