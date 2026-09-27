/** Shared Proxmox VE import type definitions for the frontend. */

export type ProxmoxNodeType = 'proxmox' | 'vm' | 'lxc'

export interface ProxmoxNode {
  id: string
  label: string
  type: ProxmoxNodeType
  ieee_address: string
  hostname?: string | null
  ip?: string | null
  status: string
  cpu_count?: number | null
  ram_gb?: number | null
  disk_gb?: number | null
  vendor?: string | null
  model?: string | null
  parent_ieee?: string | null
  // The Device Inventory row this node draws, stamped by the canvas import.
  // The canvas carries it back on save so the node links to that row rather
  // than minting a second one for the same guest.
  device_id?: string | null
}

export interface ProxmoxEdge {
  source: string
  target: string
}

export interface ProxmoxImportResponse {
  nodes: ProxmoxNode[]
  edges: ProxmoxEdge[]
  device_count: number
}
