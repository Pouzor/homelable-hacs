import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import {
  Server, Layers,
  Search, RefreshCw, X, CheckCircle2, EyeOff, Trash2, Loader2, ServerCog,
} from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { scanApi, type DuplicateNodeConflict } from '@/api/ha'
import { useCanvasStore } from '@/stores/canvasStore'
import { useDesignStore } from '@/stores/designStore'
import { useThemeStore } from '@/stores/themeStore'
import { resolveNodeColors } from '@/utils/nodeColors'
import { NODE_TYPE_DEFAULT_ICONS } from '@/utils/nodeIcons'
import { toast } from 'sonner'
import { PendingDeviceModal, type PendingDevice } from '@/components/modals/PendingDeviceModal'
import type { NodeType, ServiceInfo } from '@/types'
import { buildZigbeeProperties, isZigbeeType } from '@/utils/zigbeeProperties'
import { buildZwaveProperties, isZwaveType } from '@/utils/zwaveProperties'
import { sourceBuckets, orderedSources, isRackDevice, SOURCE_META, type SourceBucket } from '@/utils/pendingSources'
import { isRackable } from '@/utils/rackable'
import { buildMacProperty } from '@/utils/macProperty'
import { formatRelative, formatTimestamp } from '@/utils/timeFormat'
import { getCenteredPosition } from '@/utils/viewportCenter'
import { applyAutoEdges, type AutoEdge } from '@/utils/autoEdges'
import { isTypingTarget } from '@/utils/keyboard'

const STANDALONE = import.meta.env.VITE_STANDALONE === 'true'

interface PendingDevicesModalProps {
  open: boolean
  onClose: () => void
  highlightId?: string
  initialStatus?: 'pending' | 'hidden'
  /**
   * Picker mode. Clicking a card hands the device back instead of opening its
   * detail modal, so another feature (the rack canvas) can reuse this list —
   * search, filters, badges and all — rather than reimplementing a `<select>`.
   */
  onPick?: (device: PendingDevice) => void
  /** Start with the Rackable filter on (what the rack picker wants). */
  initialRackableOnly?: boolean
}

const PORT_COLORS: Record<number, string> = {
  22: '#a855f7',
  80: '#00d4ff',
  443: '#39d353',
  53: '#e3b341',
  3306: '#a855f7',
  5432: '#a855f7',
  6379: '#f85149',
  9090: '#e3b341',
  3000: '#00d4ff',
  8080: '#00d4ff',
  8443: '#39d353',
}

const CATEGORY_COLORS: Record<string, string> = {
  hypervisor: '#ff6e00',
  nas: '#39d353',
  automation: '#a855f7',
  containers: '#00d4ff',
  network: '#39d353',
  security: '#f85149',
  monitoring: '#e3b341',
  database: '#a855f7',
  web: '#00d4ff',
  media: '#ff6e00',
  iot: '#e3b341',
}

function serviceColor(port: number | null | undefined, category?: string | null): string {
  if (port != null && PORT_COLORS[port]) return PORT_COLORS[port]
  if (category && CATEGORY_COLORS[category.toLowerCase()]) return CATEGORY_COLORS[category.toLowerCase()]
  return '#8b949e'
}

type SourceFilter = 'all' | SourceBucket
type StatusFilter = 'pending' | 'hidden'

const COMMON_PORTS = new Set([22, 80, 443])

/**
 * What the device is. The curated `type` wins over the discovery guess: a
 * device drawn on a canvas only ever has the former, and an edit that retypes a
 * scanned row must not keep reading the scanner's opinion.
 */
function deviceType(d: PendingDevice): NodeType | null {
  return ((d.type ?? d.suggested_type) as NodeType) ?? null
}

function specialServiceName(d: PendingDevice): string | undefined {
  const candidates = (d.services ?? []).filter(
    (s) => s.category != null && s.port != null && !COMMON_PORTS.has(s.port) && s.service_name,
  )
  const nonWeb = candidates.find((s) => s.category?.toLowerCase() !== 'web')
  return (nonWeb ?? candidates[0])?.service_name ?? undefined
}

function deviceLabel(d: PendingDevice): string {
  // Same precedence as the detail modal — the name the user gave it first.
  return d.label ?? d.friendly_name ?? d.hostname ?? specialServiceName(d) ?? d.ip ?? d.ieee_address ?? 'device'
}

/** Server auto-edge as returned by approve/bulkApprove (camelCase handles). */
interface ServerAutoEdge {
  id: string
  source: string
  target: string
  type?: string
  sourceHandle?: string | null
  targetHandle?: string | null
  source_handle?: string | null
  target_handle?: string | null
}

function injectAutoEdges(edges: ServerAutoEdge[] | undefined) {
  if (!edges || edges.length === 0) return
  // Honor the server's edge type + handle IDs (iot bottom→top-t for mesh links,
  // cluster right→left for Proxmox hosts) and bump the referenced nodes' side
  // handle counts so left/right connection points actually exist — otherwise
  // React Flow can't resolve the handle and falls back to the top handle.
  const autoEdges: AutoEdge[] = edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    type: e.type,
    source_handle: e.source_handle ?? e.sourceHandle,
    target_handle: e.target_handle ?? e.targetHandle,
  }))
  useCanvasStore.setState((state) => {
    const next = applyAutoEdges(state.nodes, state.edges, autoEdges)
    return { nodes: next.nodes, edges: next.edges, hasUnsavedChanges: true }
  })
}

export function PendingDevicesModal({ open, onClose, highlightId, initialStatus = 'pending', onPick, initialRackableOnly = false }: PendingDevicesModalProps) {
  const [devices, setDevices] = useState<PendingDevice[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<PendingDevice | null>(null)
  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState('')
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all')
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>(initialStatus)
  // Inventory shows on-canvas devices by default; toggle off to hide them.
  const [showOnCanvas, setShowOnCanvas] = useState(true)
  // Optionally restrict to devices that have at least one detected service.
  const [withServicesOnly, setWithServicesOnly] = useState(false)
  // Hardware only — what a rack can hold. On by default when the rack canvas
  // opens the inventory to pick a mount.
  const [rackableOnly, setRackableOnly] = useState(initialRackableOnly)
  const { addNode, scanEventTs } = useCanvasStore()
  const setSelectedNode = useCanvasStore((s) => s.setSelectedNode)
  const activeDesignId = useDesignStore((s) => s.activeDesignId)
  const highlightRef = useRef<HTMLButtonElement>(null)
  // Set when a single approve is refused because the same host is already on
  // this design — the user decides: link to the existing node or duplicate.
  const [dupPrompt, setDupPrompt] = useState<{ device: PendingDevice; conflict: DuplicateNodeConflict } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = statusFilter === 'pending' ? await scanApi.pending() : await scanApi.hidden()
      setDevices(res.data as PendingDevice[])
    } catch {
      toast.error(`Failed to load ${statusFilter} devices`)
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => { if (open) load() }, [open, load])
  useEffect(() => { if (open && scanEventTs > 0) load() }, [scanEventTs, open, load])

  useEffect(() => {
    if (!open) {
      setSelectMode(false)
      setSelectedIds(new Set())
      setSearch('')
    } else {
      setStatusFilter(initialStatus)
      // Reopening from the rack picker must re-arm the filter even if the user
      // turned it off during the previous visit.
      setRackableOnly(initialRackableOnly)
    }
  }, [open, initialStatus, initialRackableOnly])

  const distinctTypes = useMemo(() => {
    const set = new Set<string>()
    devices.forEach((d) => { const t = deviceType(d); if (t) set.add(t) })
    return [...set].sort()
  }, [devices])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return devices.filter((d) => {
      // A device merged across sources (e.g. IP scan + Proxmox) matches every
      // filter for a source that has seen it.
      if (sourceFilter !== 'all' && !sourceBuckets(d).has(sourceFilter)) return false
      if (typeFilter !== 'all' && deviceType(d) !== typeFilter) return false
      // Inventory-only: optionally hide devices already placed on a canvas.
      if (statusFilter === 'pending' && !showOnCanvas && (d.canvas_count ?? 0) > 0) return false
      if (withServicesOnly && (d.services?.length ?? 0) === 0) return false
      if (rackableOnly && !isRackable(d)) return false
      if (q) {
        const hay = [
          d.friendly_name, d.hostname, d.ip, d.mac, d.ieee_address, d.vendor, d.model,
          ...d.services.map((s) => s.service_name),
        ].filter(Boolean).join(' ').toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [devices, search, sourceFilter, typeFilter, statusFilter, showOnCanvas, withServicesOnly, rackableOnly])

  useEffect(() => {
    if (!highlightId || loading || !open) return
    highlightRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [highlightId, loading, open, filtered])

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const handleCardClick = (d: PendingDevice) => {
    // Picker mode wins over everything else: the caller opened this list to get
    // one device back, not to approve or hide anything.
    if (onPick) { onPick(d); return }
    if (selectMode) { toggleSelect(d.id); return }
    if (statusFilter === 'hidden') { handleRestore(d); return }
    setSelected(d)
  }

  const handleRestore = async (device: PendingDevice) => {
    try {
      await scanApi.restore(device.id)
      setDevices((prev) => prev.filter((d) => d.id !== device.id))
      toast.success(`Restored ${deviceLabel(device)}`)
    } catch {
      toast.error('Failed to restore device')
    }
  }

  const handleBulkRestore = async () => {
    const ids = [...selectedIds]
    if (ids.length === 0) return
    try {
      const res = await scanApi.bulkRestore(ids)
      setDevices((prev) => prev.filter((d) => !ids.includes(d.id)))
      setSelectedIds(new Set())
      toast.success(`Restored ${res.data.restored} device${res.data.restored !== 1 ? 's' : ''}`)
    } catch {
      toast.error('Failed to bulk restore devices')
    }
  }

  const enterSelectMode = () => setSelectMode(true)
  const exitSelectMode = () => { setSelectMode(false); setSelectedIds(new Set()) }
  const selectAllVisible = () => setSelectedIds(new Set(filtered.map((d) => d.id)))

  const handleClearAll = async () => {
    const targets = filtered
    if (targets.length === 0) return
    const filtersActive = targets.length !== devices.length
    try {
      if (filtersActive) {
        const results = await Promise.allSettled(targets.map((d) => scanApi.ignore(d.id)))
        const failed = results.filter((r) => r.status === 'rejected').length
        const removedIds = new Set(
          targets.filter((_, i) => results[i].status === 'fulfilled').map((d) => d.id)
        )
        setDevices((prev) => prev.filter((d) => !removedIds.has(d.id)))
        setSelectedIds(new Set())
        if (failed > 0) toast.error(`Removed ${removedIds.size}, ${failed} failed`)
        else toast.success(`Removed ${removedIds.size} device${removedIds.size !== 1 ? 's' : ''}`)
      } else {
        // Clears only pending rows server-side; approved/on-canvas devices stay,
        // so reload rather than blanking the whole inventory.
        await scanApi.clearPending()
        setSelectedIds(new Set())
        await load()
        toast.success('Pending devices cleared')
      }
    } catch {
      toast.error('Failed to clear pending devices')
    }
  }

  // force=true is sent only after the user confirms they want a duplicate node
  // on this design (the backend otherwise returns a `duplicate` conflict so we
  // can ask instead of silently doubling the card).
  const approveDevice = async (device: PendingDevice, force = false) => {
    // Rack gear documents a mount, not a host: it stays out of logical canvases.
    // The backend refuses it too — this is the friendly half of the guard.
    if (isRackDevice(device)) {
      toast.error('Rack devices belong to a rack canvas, not a logical one')
      return
    }
    const fallbackLabel = deviceLabel(device)
    const type = deviceType(device) ?? 'generic'
    const zwave = isZwaveType(type)
    const wireless = isZigbeeType(type) || zwave
    const nodeData = {
      label: fallbackLabel,
      type,
      ip: device.ip ?? undefined,
      mac: device.mac ?? undefined,
      hostname: device.hostname ?? undefined,
      status: wireless ? 'online' : 'unknown',
      services: (device.services ?? []) as ServiceInfo[],
      // Non-wireless devices keep the hidden rows the backend already put on
      // the pending device (e.g. Proxmox VMID/CPU/RAM/Disk/Kind/Source) and add
      // MAC — rebuilding from scratch would drop the Proxmox specs.
      properties: zwave
        ? buildZwaveProperties(device)
        : isZigbeeType(type)
          ? buildZigbeeProperties(device)
          : [...(device.properties ?? []), ...buildMacProperty(device.mac)],
    }
    try {
      const res = await scanApi.approve(device.id, { ...nodeData, force }, activeDesignId)
      // Same host already on this design: ask before creating a second card
      // (only on the first, non-forced attempt).
      if (res.data.duplicate && !force) {
        // Close the device-detail modal first so the prompt isn't trapped
        // behind another open dialog.
        setSelected(null)
        setDupPrompt({ device, conflict: res.data.duplicate })
        return
      }
      const nodeId = res.data.node_id
      if (!nodeId) { toast.error('Failed to approve device'); return }
      addNode({
        id: nodeId,
        type: nodeData.type,
        position: getCenteredPosition(),
        data: {
          ...nodeData,
          status: wireless ? ('online' as const) : ('unknown' as const),
          // The row this node draws — lets an inventory edit reach it on screen.
          device_id: res.data.node?.device_id ?? device.id,
        },
      })
      injectAutoEdges(res.data.edges)
      const created = res.data.edges_created ?? 0
      const extra = created > 0 ? ` (+${created} link${created !== 1 ? 's' : ''})` : ''
      toast.success(`Approved ${nodeData.label}${extra}`)
      // Keep the row (now on-canvas, shown with an "In N canvas" badge); reload
      // for a fresh canvas_count rather than dropping it until reopen.
      setSelected(null)
      setDupPrompt(null)
      await load()
    } catch {
      toast.error('Failed to approve device')
    }
  }

  const handleApprove = (device: PendingDevice) => approveDevice(device, false)

  const goToExistingNode = (nodeId: string) => {
    setSelectedNode(nodeId)
    setDupPrompt(null)
    setSelected(null)
    onClose()
  }

  const handleHide = async (device: PendingDevice) => {
    try {
      await scanApi.hide(device.id)
      setDevices((prev) => prev.filter((d) => d.id !== device.id))
      setSelected(null)
      toast.success('Device hidden')
    } catch {
      toast.error('Failed to hide device')
    }
  }

  const handleIgnore = async (device: PendingDevice) => {
    try {
      await scanApi.ignore(device.id)
      setDevices((prev) => prev.filter((d) => d.id !== device.id))
      setSelected(null)
    } catch (err) {
      // The row holds the facts of the nodes drawing it, so the integration
      // refuses to drop it while one does.
      if ((err as { code?: string } | null)?.code === 'in_use') {
        toast.error('This device is on a canvas — delete its node or hide it instead')
      } else {
        toast.error('Failed to remove device')
      }
    }
  }

  const handleBulkApprove = async () => {
    const rackIds = new Set(devices.filter(isRackDevice).map((d) => d.id))
    const ids = [...selectedIds].filter((id) => !rackIds.has(id))
    const skippedRack = selectedIds.size - ids.length
    if (skippedRack > 0) {
      toast.error(
        `${skippedRack} rack device${skippedRack > 1 ? 's' : ''} skipped — they belong to a rack canvas`,
      )
    }
    if (ids.length === 0) return
    try {
      const res = await scanApi.bulkApprove(ids, {}, activeDesignId)
      const deviceToNode: Record<string, string> = {}
      res.data.device_ids.forEach((did, i) => { deviceToNode[did] = res.data.node_ids[i] })
      const approvedDevices = devices.filter((d) => ids.includes(d.id))
      const cols = Math.min(4, approvedDevices.length)
      const rows = Math.ceil(approvedDevices.length / 4)
      const origin = getCenteredPosition(cols * 160, rows * 100)
      approvedDevices.forEach((d, i) => {
        const nodeId = deviceToNode[d.id]
        if (!nodeId) return
        const type = deviceType(d) ?? 'generic'
        const zwave = isZwaveType(type)
        const wireless = isZigbeeType(type) || zwave
        addNode({
          id: nodeId,
          type,
          position: { x: origin.x + (i % 4) * 160, y: origin.y + Math.floor(i / 4) * 100 },
          data: {
            label: deviceLabel(d),
            type,
            device_id: d.id,
            ip: d.ip ?? undefined,
            mac: d.mac ?? undefined,
            hostname: d.hostname ?? undefined,
            status: wireless ? ('online' as const) : ('unknown' as const),
            services: (d.services ?? []) as ServiceInfo[],
            // Keep the backend's hidden rows (Proxmox specs) and add MAC.
            properties: zwave
              ? buildZwaveProperties(d)
              : isZigbeeType(type)
                ? buildZigbeeProperties(d)
                : [...(d.properties ?? []), ...buildMacProperty(d.mac)],
          },
        })
      })
      injectAutoEdges(res.data.edges)
      // Don't strip approved rows locally: the inventory keeps them with an
      // "In N canvas" badge (that's what showOnCanvas toggles). Reload so they
      // reappear with a fresh canvas_count instead of vanishing until reopen.
      setSelectedIds(new Set())
      await load()
      const linkExtra = res.data.edges_created > 0 ? ` (+${res.data.edges_created} link${res.data.edges_created !== 1 ? 's' : ''})` : ''
      toast.success(`Approved ${res.data.approved} device${res.data.approved !== 1 ? 's' : ''}${linkExtra}`)
      // Bulk can't prompt per-device, so report the ones already on this canvas
      // (skipped as duplicates) instead of silently dropping them.
      const dupes = res.data.skipped_devices ?? []
      if (dupes.length > 0) {
        const names = dupes.slice(0, 3).map((d) => d.label).join(', ')
        const more = dupes.length > 3 ? ` +${dupes.length - 3} more` : ''
        toast.info(
          `${dupes.length} already on this canvas, skipped: ${names}${more}`,
          { description: 'Matched an existing node by IP/MAC/IEEE on this design.' },
        )
      }
    } catch {
      toast.error('Failed to bulk approve devices')
    }
  }

  const handleBulkHide = async () => {
    const ids = [...selectedIds]
    if (ids.length === 0) return
    try {
      const res = await scanApi.bulkHide(ids)
      setDevices((prev) => prev.filter((d) => !ids.includes(d.id)))
      setSelectedIds(new Set())
      toast.success(`Hidden ${res.data.hidden} device${res.data.hidden !== 1 ? 's' : ''}`)
    } catch {
      toast.error('Failed to bulk hide devices')
    }
  }

  const searchRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      const inField = isTypingTarget(e)
      if (e.key === 'Escape') {
        if (selectMode && selectedIds.size > 0) { e.preventDefault(); setSelectedIds(new Set()) }
        return
      }
      if (inField) return
      if (e.key === '/') { e.preventDefault(); searchRef.current?.focus() }
      // Bulk actions are meaningless in picker mode — the card click returns a
      // device, so select mode must stay unreachable from the keyboard too.
      else if (onPick) return
      else if (e.key.toLowerCase() === 's') { e.preventDefault(); if (selectMode) exitSelectMode(); else enterSelectMode() }
      else if (e.key.toLowerCase() === 'a' && selectMode) { e.preventDefault(); selectAllVisible() }
      else if (e.key === 'Enter' && selectMode && selectedIds.size > 0) { e.preventDefault(); handleBulkApprove() }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, selectMode, selectedIds, filtered, onPick])

  return (
    <>
      <Dialog open={open} onOpenChange={(v) => { if (!v) onClose() }}>
        <DialogContent
          showCloseButton={false}
          className="!max-w-none w-[95vw] h-[90vh] p-0 flex flex-col gap-0 bg-[#0d1117] border-border"
        >
          <DialogHeader className="px-4 py-3 border-b border-border shrink-0">
            <div className="flex items-center justify-between gap-3">
              <DialogTitle className="text-base font-semibold flex items-center gap-2">
                {onPick ? 'Pick a Device' : statusFilter === 'pending' ? 'Device Inventory' : 'Hidden Devices'}
                <span className="text-muted-foreground font-normal text-xs">
                  ({filtered.length}{filtered.length !== devices.length && ` of ${devices.length}`})
                </span>
              </DialogTitle>
              <div className="flex items-center gap-1">
                <button onClick={load} className="text-muted-foreground hover:text-foreground p-1.5 rounded transition-colors" title="Refresh">
                  <RefreshCw size={14} />
                </button>
                {/* Never offer a destructive bulk clear from a picker. */}
                {!onPick && statusFilter === 'pending' && devices.length > 0 && (
                  <button
                    onClick={handleClearAll}
                    className="text-muted-foreground hover:text-[#f85149] p-1.5 rounded transition-colors"
                    title={filtered.length !== devices.length ? `Remove ${filtered.length} filtered` : 'Clear all pending'}
                  >
                    <Trash2 size={14} />
                  </button>
                )}
                <button onClick={onClose} className="text-muted-foreground hover:text-foreground p-1.5 rounded transition-colors" title="Close">
                  <X size={14} />
                </button>
              </div>
            </div>
          </DialogHeader>

          <div className="px-4 py-2 border-b border-border bg-[#161b22] shrink-0 flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[200px] max-w-md">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                ref={searchRef}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search name, IP, MAC, IEEE, service…"
                className="w-full text-xs bg-[#0d1117] border border-border rounded px-7 py-1.5 outline-none focus:border-[#00d4ff]/50"
              />
            </div>
            <div className="flex rounded border border-border overflow-hidden text-xs" role="group" aria-label="Source filter">
              <button
                onClick={() => setSourceFilter('all')}
                className={`px-2.5 py-1.5 transition-colors ${sourceFilter === 'all' ? 'bg-[#00d4ff]/20 text-[#00d4ff]' : 'bg-[#0d1117] text-muted-foreground hover:text-foreground'}`}
              >
                All
              </button>
              <button
                onClick={() => setSourceFilter('ip')}
                className={`px-2.5 py-1.5 transition-colors border-l border-border ${sourceFilter === 'ip' ? 'bg-[#a855f7]/20 text-[#a855f7]' : 'bg-[#0d1117] text-muted-foreground hover:text-foreground'}`}
              >
                IP scan
              </button>
              <button
                onClick={() => setSourceFilter('zigbee')}
                className={`px-2.5 py-1.5 transition-colors border-l border-border ${sourceFilter === 'zigbee' ? 'bg-[#00d4ff]/20 text-[#00d4ff]' : 'bg-[#0d1117] text-muted-foreground hover:text-foreground'}`}
              >
                Zigbee
              </button>
              <button
                onClick={() => setSourceFilter('zwave')}
                className={`px-2.5 py-1.5 transition-colors border-l border-border ${sourceFilter === 'zwave' ? 'bg-[#ff6e00]/20 text-[#ff6e00]' : 'bg-[#0d1117] text-muted-foreground hover:text-foreground'}`}
              >
                Z-Wave
              </button>
              {!STANDALONE && (
                <button
                  onClick={() => setSourceFilter('proxmox')}
                  className={`px-2.5 py-1.5 transition-colors border-l border-border ${sourceFilter === 'proxmox' ? 'bg-[#e57000]/20 text-[#e57000]' : 'bg-[#0d1117] text-muted-foreground hover:text-foreground'}`}
                >
                  Proxmox
                </button>
              )}
              <button
                onClick={() => setSourceFilter('rack')}
                className={`px-2.5 py-1.5 transition-colors border-l border-border ${sourceFilter === 'rack' ? 'bg-[#39d353]/20 text-[#39d353]' : 'bg-[#0d1117] text-muted-foreground hover:text-foreground'}`}
                title="Gear created from a rack canvas"
              >
                Rack devices
              </button>
              <button
                onClick={() => setSourceFilter('canvas')}
                className={`px-2.5 py-1.5 transition-colors border-l border-border ${sourceFilter === 'canvas' ? 'bg-[#8b949e]/20 text-foreground' : 'bg-[#0d1117] text-muted-foreground hover:text-foreground'}`}
                title="Documented directly on a canvas — no scan ever saw it"
              >
                Canvas
              </button>
            </div>
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="text-xs bg-[#0d1117] border border-border rounded px-2 py-1.5 outline-none focus:border-[#00d4ff]/50"
              aria-label="Type filter"
            >
              <option value="all">All types</option>
              {distinctTypes.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <div className="flex rounded border border-border overflow-hidden text-xs">
              <button
                onClick={() => setStatusFilter('pending')}
                className={`px-2.5 py-1.5 transition-colors ${statusFilter === 'pending' ? 'bg-[#00d4ff]/20 text-[#00d4ff]' : 'bg-[#0d1117] text-muted-foreground hover:text-foreground'}`}
              >
                Inventory
              </button>
              <button
                onClick={() => setStatusFilter('hidden')}
                className={`px-2.5 py-1.5 transition-colors ${statusFilter === 'hidden' ? 'bg-[#8b949e]/20 text-foreground' : 'bg-[#0d1117] text-muted-foreground hover:text-foreground'}`}
              >
                Hidden
              </button>
            </div>
            {statusFilter === 'pending' && (
              <button
                onClick={() => setShowOnCanvas((v) => !v)}
                className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border transition-colors ${showOnCanvas ? 'bg-[#0d1117] text-muted-foreground border-border hover:text-foreground' : 'bg-[#00d4ff]/20 text-[#00d4ff] border-[#00d4ff]/50'}`}
                title="Show or hide devices already on a canvas"
                aria-pressed={!showOnCanvas}
              >
                <Layers size={12} />
                {showOnCanvas ? 'Hide on-canvas' : 'Show on-canvas'}
              </button>
            )}
            <button
              onClick={() => setWithServicesOnly((v) => !v)}
              className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border transition-colors ${withServicesOnly ? 'bg-[#00d4ff]/20 text-[#00d4ff] border-[#00d4ff]/50' : 'bg-[#0d1117] text-muted-foreground border-border hover:text-foreground'}`}
              title="Only show devices with at least one detected service"
              aria-pressed={withServicesOnly}
            >
              <ServerCog size={12} />
              With services
            </button>
            <button
              onClick={() => setRackableOnly((v) => !v)}
              className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border transition-colors ${rackableOnly ? 'bg-[#39d353]/20 text-[#39d353] border-[#39d353]/50' : 'bg-[#0d1117] text-muted-foreground border-border hover:text-foreground'}`}
              title="Only show hardware you could mount in a rack (no VMs, containers or mesh devices)"
              aria-pressed={rackableOnly}
            >
              <Server size={12} />
              Rackable
            </button>
            {!onPick && (
              <button
                onClick={() => selectMode ? exitSelectMode() : enterSelectMode()}
                className={`text-xs px-2.5 py-1.5 rounded border transition-colors ${selectMode ? 'bg-[#00d4ff]/20 text-[#00d4ff] border-[#00d4ff]/50' : 'bg-[#0d1117] text-muted-foreground border-border hover:text-foreground'}`}
                title="Toggle select mode (s)"
              >
                {selectMode ? 'Exit select' : 'Select mode'}
              </button>
            )}
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto p-4">
            {loading && (
              <div className="flex items-center justify-center py-10">
                <Loader2 size={20} className="animate-spin text-muted-foreground" />
              </div>
            )}
            {!loading && filtered.length === 0 && (
              <p className="text-xs text-muted-foreground text-center py-10">
                {devices.length === 0 ? `No ${statusFilter} devices` : 'No devices match filters'}
              </p>
            )}
            {!loading && filtered.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
                {filtered.map((d) => (
                  <DeviceCard
                    key={d.id}
                    device={d}
                    selected={selectedIds.has(d.id)}
                    selectMode={selectMode}
                    highlighted={d.id === highlightId}
                    onClick={() => handleCardClick(d)}
                    cardRef={d.id === highlightId ? highlightRef : undefined}
                  />
                ))}
              </div>
            )}
          </div>

          {selectMode && (
            <div className="px-4 py-2.5 border-t border-border bg-[#161b22] shrink-0 flex items-center gap-2 flex-wrap">
              <span className="text-xs text-muted-foreground mr-1">
                {selectedIds.size} selected
              </span>
              <button
                onClick={selectAllVisible}
                className="text-xs px-2.5 py-1.5 rounded border border-border text-muted-foreground hover:text-foreground transition-colors"
              >
                Select all visible ({filtered.length})
              </button>
              <button
                onClick={() => setSelectedIds(new Set())}
                disabled={selectedIds.size === 0}
                className="text-xs px-2.5 py-1.5 rounded border border-border text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors"
              >
                Clear
              </button>
              <div className="flex-1" />
              {statusFilter === 'pending' && (
                <>
                  <button
                    onClick={handleBulkApprove}
                    disabled={selectedIds.size === 0}
                    className="text-xs px-3 py-1.5 rounded bg-[#39d353]/20 text-[#39d353] hover:bg-[#39d353]/30 disabled:opacity-40 font-medium transition-colors"
                  >
                    Approve ({selectedIds.size})
                  </button>
                  <button
                    onClick={handleBulkHide}
                    disabled={selectedIds.size === 0}
                    className="text-xs px-3 py-1.5 rounded bg-[#8b949e]/20 text-[#8b949e] hover:bg-[#8b949e]/30 disabled:opacity-40 font-medium transition-colors"
                  >
                    Hide ({selectedIds.size})
                  </button>
                </>
              )}
              {statusFilter === 'hidden' && (
                <button
                  onClick={handleBulkRestore}
                  disabled={selectedIds.size === 0}
                  className="text-xs px-3 py-1.5 rounded bg-[#e3b341]/20 text-[#e3b341] hover:bg-[#e3b341]/30 disabled:opacity-40 font-medium transition-colors"
                >
                  Restore ({selectedIds.size})
                </button>
              )}
            </div>
          )}

          {/* Duplicate confirmation: the host is already on this design. Ask
              before creating a second card — never silently drop or duplicate.
              Rendered INSIDE the inventory DialogContent on purpose: an open
              dialog marks outside content inert/aria-hidden, so a sibling
              overlay would be unclickable. Keeping it in the dialog's own
              subtree avoids that. */}
          {dupPrompt && (
            <div
              role="dialog"
              aria-modal="true"
              aria-label="Device already on this canvas"
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
              onClick={() => setDupPrompt(null)}
            >
              <div
                className="w-full max-w-md rounded-lg border border-border bg-[#161b22] p-5 shadow-xl"
                onClick={(e) => e.stopPropagation()}
              >
                <h2 className="text-base font-semibold text-foreground mb-3">Device already on this canvas</h2>
                <div className="text-sm text-muted-foreground space-y-3">
                  <p>
                    <span className="text-foreground font-medium break-all">{deviceLabel(dupPrompt.device)}</span>{' '}
                    matches a node already on this design
                    {' '}(<span className="uppercase text-[11px] font-mono">{dupPrompt.conflict.match}</span>{' '}
                    <span className="font-mono text-foreground">{dupPrompt.conflict.value}</span>)
                    {dupPrompt.conflict.existing_label ? (
                      <>: <span className="text-foreground font-medium break-all">{dupPrompt.conflict.existing_label}</span></>
                    ) : null}.
                  </p>
                  <p>Add it again anyway, or jump to the existing node?</p>
                  <div className="flex flex-wrap justify-end gap-2 pt-1">
                    <button
                      onClick={() => setDupPrompt(null)}
                      className="text-xs px-3 py-1.5 rounded border border-border text-muted-foreground hover:text-foreground transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() => goToExistingNode(dupPrompt.conflict.existing_node_id)}
                      className="text-xs px-3 py-1.5 rounded bg-[#00d4ff]/20 text-[#00d4ff] hover:bg-[#00d4ff]/30 font-medium transition-colors"
                    >
                      Go to existing node
                    </button>
                    <button
                      onClick={() => approveDevice(dupPrompt.device, true)}
                      className="text-xs px-3 py-1.5 rounded bg-[#e3b341]/20 text-[#e3b341] hover:bg-[#e3b341]/30 font-medium transition-colors"
                    >
                      Add duplicate anyway
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <PendingDeviceModal
        // A fresh form per device: the grid stays mounted across card clicks.
        key={selected?.id ?? 'none'}
        device={selected}
        onClose={() => setSelected(null)}
        onApprove={handleApprove}
        onHide={handleHide}
        onIgnore={handleIgnore}
        onSaved={(saved) => {
          // Patch in place rather than refetch: the grid keeps its scroll
          // position and the open detail modal keeps showing the same device.
          setDevices((prev) => prev.map((d) => (d.id === saved.id ? saved : d)))
          setSelected(saved)
        }}
      />
    </>
  )
}

interface DeviceCardProps {
  device: PendingDevice
  selected: boolean
  selectMode: boolean
  highlighted: boolean
  onClick: () => void
  cardRef?: React.Ref<HTMLButtonElement>
}

function DeviceCard({ device, selected, selectMode, highlighted, onClick, cardRef }: DeviceCardProps) {
  const roleType = deviceType(device) ?? 'generic'
  const Icon = NODE_TYPE_DEFAULT_ICONS[roleType] ?? NODE_TYPE_DEFAULT_ICONS.generic
  const activeTheme = useThemeStore((s) => s.activeTheme)
  // Colour the role badge with the same accent the node uses on the canvas
  // (from the active theme / style section), instead of a flat grey.
  const roleColor = resolveNodeColors({ type: roleType, custom_colors: undefined }, activeTheme).border
  const label = deviceLabel(device)
  // A device may carry more than one source (e.g. IP scan + Proxmox); render one
  // badge per source so the merge is visible.
  const sourceBadges = orderedSources(device)
  const services = device.services ?? []
  const visibleServices = services.slice(0, 4)
  const moreServices = services.length - visibleServices.length

  // Timestamps: a device placed on a canvas shows its linked node's lifecycle
  // (created / last scan / last modified / last seen). A device that is only in
  // the discovery inventory has no node yet, so it falls back to when the
  // scanner first saw it.
  const onCanvas = (device.canvas_count ?? 0) > 0
  const timestamps: { label: string; iso: string }[] = []
  if (onCanvas) {
    if (device.node_created_at) timestamps.push({ label: 'Created', iso: device.node_created_at })
    if (device.node_last_scan) timestamps.push({ label: 'Scan', iso: device.node_last_scan })
    if (device.node_last_modified) timestamps.push({ label: 'Modified', iso: device.node_last_modified })
    if (device.node_last_seen) timestamps.push({ label: 'Seen', iso: device.node_last_seen })
  }
  if (timestamps.length === 0) {
    timestamps.push({ label: 'Discovered', iso: device.discovered_at })
  }

  const borderClass = highlighted
    ? 'border-[#e3b341] bg-[#2d3748]'
    : selected
    ? 'border-[#00d4ff] bg-[#00d4ff]/5 shadow-[0_0_0_1px_rgba(0,212,255,0.4)] scale-[1.02]'
    : 'border-border bg-[#161b22] hover:border-[#30363d] hover:bg-[#21262d]'

  return (
    <button
      ref={cardRef}
      onClick={onClick}
      data-testid={`pending-card-${device.id}`}
      className={`relative text-left rounded-lg border p-2.5 transition-all duration-150 ${borderClass}`}
    >
      {selectMode && selected && (
        <CheckCircle2
          size={18}
          className="absolute top-2 right-2 text-[#00d4ff] fill-[#0d1117] z-10"
        />
      )}
      {!selectMode && device.status === 'hidden' && (
        <EyeOff size={14} className="absolute top-2 right-2 text-muted-foreground" />
      )}
      {/* Canvas-presence corner: how many canvases this device already sits on.
          Hidden while a select-mode checkmark occupies the same corner. */}
      {device.status !== 'hidden' && (device.canvas_count ?? 0) > 0 && !(selectMode && selected) && (
        <div
          className="absolute top-0 right-0 flex items-center gap-1 rounded-bl-lg rounded-tr-lg bg-[#00d4ff] text-[#0d1117] text-xs font-bold px-2 py-1 shadow-md"
          title={`On ${device.canvas_count} canvas${device.canvas_count !== 1 ? 'es' : ''}`}
          aria-label={`On ${device.canvas_count} canvas${device.canvas_count !== 1 ? 'es' : ''}`}
        >
          <Layers size={12} strokeWidth={2.5} />
          {device.canvas_count}
        </div>
      )}

      <div className="flex items-start gap-2 mb-2">
        <div className="shrink-0 w-8 h-8 rounded bg-[#21262d] flex items-center justify-center text-foreground">
          <Icon size={16} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-foreground break-all leading-snug">{label}</div>
          <div className="flex items-center gap-1 mt-0.5 flex-wrap">
            {sourceBadges.map((bucket) => (
              <span
                key={bucket}
                className="text-[9px] font-mono px-1.5 py-0.5 rounded uppercase tracking-wider"
                style={{ background: `${SOURCE_META[bucket].color}22`, color: SOURCE_META[bucket].color }}
              >
                {SOURCE_META[bucket].label}
              </span>
            ))}
            {roleType !== 'generic' && (
              <span
                className="text-[9px] font-mono px-1.5 py-0.5 rounded uppercase tracking-wider"
                style={{ background: `${roleColor}22`, color: roleColor }}
              >
                {roleType}
              </span>
            )}
            {device.lqi != null && (
              <span className="text-[9px] font-mono px-1.5 py-0.5 rounded uppercase tracking-wider bg-[#21262d] text-muted-foreground">
                LQI {device.lqi}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[11px] mb-2">
        {device.ip && <InfoLine label="IP" value={device.ip} />}
        {device.mac && <InfoLine label="MAC" value={device.mac} />}
        {device.ieee_address && <InfoLine label="IEEE" value={device.ieee_address} />}
        {device.hostname && <InfoLine label="Host" value={device.hostname} />}
        {device.vendor && <InfoLine label="Vendor" value={device.vendor} />}
        {device.model && <InfoLine label="Model" value={device.model} />}
      </div>

      {visibleServices.length > 0 && (
        <div className="flex items-center gap-1 flex-wrap">
          {visibleServices.map((s, i) => {
            const color = serviceColor(s.port, s.category)
            return (
              <span
                key={`${s.port}-${s.protocol}-${i}`}
                className="text-[9px] font-mono px-1.5 py-0.5 rounded uppercase tracking-wider"
                style={{ background: `${color}22`, color }}
                title={`${s.service_name ?? ''} (${s.protocol}/${s.port})`}
              >
                {s.service_name}
              </span>
            )
          })}
          {moreServices > 0 && (
            <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-[#21262d] text-muted-foreground">
              +{moreServices}
            </span>
          )}
        </div>
      )}

      {/* Timestamps — compact relative times, full date on hover. Kept tiny so
          the tile footprint stays the same as before. */}
      <div className="mt-2 pt-2 border-t border-border/50 grid grid-cols-2 gap-x-2 gap-y-0.5 text-[10px]">
        {timestamps.map((t) => (
          <TimeLine key={t.label} label={t.label} iso={t.iso} />
        ))}
      </div>
    </button>
  )
}

function InfoLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-1.5 min-w-0">
      <span className="text-muted-foreground shrink-0 w-12">{label}</span>
      <span className="font-mono text-foreground truncate">{value}</span>
    </div>
  )
}

function TimeLine({ label, iso }: { label: string; iso: string }) {
  return (
    <div className="flex items-baseline gap-1 min-w-0" title={formatTimestamp(iso)}>
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className="font-mono text-foreground/80 truncate">{formatRelative(iso)}</span>
    </div>
  )
}
