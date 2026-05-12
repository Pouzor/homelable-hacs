import { useState } from 'react'
import { Network, Router, Cpu, Loader2, Plus, AlertTriangle } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { zigbeeApi } from '@/api/ha'
import { toast } from 'sonner'
import type { ZigbeeNode } from './types'

/**
 * Zigbee2MQTT import modal — HA build.
 *
 * Differs from the standalone modal:
 *  - No MQTT host/port/user/pass/TLS form. HA's MQTT integration owns the
 *    broker; the Z2M base topic is set in the options flow.
 *  - No "send to canvas" direct mode. Selected devices are pushed to the
 *    pending devices store; user approves them from there.
 */

interface ZigbeeImportModalProps {
  open: boolean
  onClose: () => void
  onImported?: (added: number) => void
}

const DEVICE_TYPE_ICON = {
  zigbee_coordinator: Network,
  zigbee_router: Router,
  zigbee_enddevice: Cpu,
} as const

const DEVICE_TYPE_LABEL = {
  zigbee_coordinator: 'Coordinator',
  zigbee_router: 'Router',
  zigbee_enddevice: 'End Device',
} as const

const DEVICE_TYPE_COLOR = {
  zigbee_coordinator: '#00d4ff',
  zigbee_router: '#39d353',
  zigbee_enddevice: '#e3b341',
} as const

interface WsErrorShape {
  code?: string
  message?: string
}

function extractWsError(err: unknown): WsErrorShape {
  if (err && typeof err === 'object') {
    const e = err as { code?: string; message?: string; error?: WsErrorShape }
    if (e.error) return e.error
    return { code: e.code, message: e.message }
  }
  return {}
}

export function ZigbeeImportModal({ open, onClose, onImported }: ZigbeeImportModalProps) {
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [devices, setDevices] = useState<ZigbeeNode[]>([])
  const [baseTopic, setBaseTopic] = useState<string>('zigbee2mqtt')
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [errorBanner, setErrorBanner] = useState<{
    kind: 'mqtt_missing' | 'timeout' | 'bad_response' | 'generic'
    message: string
  } | null>(null)

  const resetAndClose = () => {
    setDevices([])
    setChecked(new Set())
    setErrorBanner(null)
    onClose()
  }

  const handleFetch = async () => {
    setLoading(true)
    setErrorBanner(null)
    try {
      const res = await zigbeeApi.fetchDevices()
      setDevices(res.data.nodes)
      setBaseTopic(res.data.base_topic)
      setChecked(new Set(res.data.nodes.map((n) => n.id)))
      if (res.data.nodes.length === 0) {
        toast.info('Network map empty — Zigbee2MQTT reports no devices')
      } else {
        toast.success(`Found ${res.data.nodes.length} device${res.data.nodes.length !== 1 ? 's' : ''}`)
      }
    } catch (err) {
      const { code, message } = extractWsError(err)
      if (code === 'mqtt_not_configured') {
        setErrorBanner({
          kind: 'mqtt_missing',
          message:
            'Home Assistant MQTT integration is not configured. Install or set it up first.',
        })
      } else if (code === 'timeout') {
        setErrorBanner({
          kind: 'timeout',
          message:
            'Timed out waiting for Zigbee2MQTT. Verify Z2M is running and the base topic matches.',
        })
      } else if (code === 'bad_response') {
        setErrorBanner({ kind: 'bad_response', message: message ?? 'Bad response from Zigbee2MQTT' })
      } else {
        setErrorBanner({ kind: 'generic', message: message ?? 'Failed to fetch Zigbee devices' })
      }
    } finally {
      setLoading(false)
    }
  }

  const toggleCheck = (id: string) =>
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })

  const toggleAll = () =>
    setChecked(checked.size === devices.length ? new Set() : new Set(devices.map((d) => d.id)))

  const handleImport = async () => {
    const selected = devices.filter((d) => checked.has(d.id))
    if (!selected.length) return
    setImporting(true)
    try {
      const res = await zigbeeApi.importDevices(selected)
      const { added, skipped } = res.data
      if (added) toast.success(`${added} device${added !== 1 ? 's' : ''} added to Pending`)
      if (skipped) toast.info(`${skipped} skipped (already known)`)
      onImported?.(added)
      resetAndClose()
    } catch (err) {
      const { message } = extractWsError(err)
      toast.error(message ?? 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  const groupedDevices = {
    zigbee_coordinator: devices.filter((d) => d.type === 'zigbee_coordinator'),
    zigbee_router: devices.filter((d) => d.type === 'zigbee_router'),
    zigbee_enddevice: devices.filter((d) => d.type === 'zigbee_enddevice'),
  } as const

  return (
    <Dialog open={open} onOpenChange={(v) => !v && resetAndClose()}>
      <DialogContent className="bg-[#161b22] border-border max-w-xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <Network size={16} className="text-[#00d4ff]" />
            Zigbee2MQTT Import
          </DialogTitle>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto space-y-3 py-2 min-h-0">
          <p className="text-xs text-muted-foreground">
            Fetches the Z2M network map via Home Assistant's MQTT integration. Base topic:{' '}
            <span className="font-mono text-foreground">{baseTopic}</span>{' '}
            (change in integration options).
          </p>

          {errorBanner && (
            <div className="flex items-start gap-2 p-2 rounded-md border border-[#f85149]/30 bg-[#f85149]/10 text-xs text-[#f85149]">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>{errorBanner.message}</span>
            </div>
          )}

          <div className="flex gap-2">
            <Button
              size="sm"
              style={{ background: '#00d4ff', color: '#0d1117' }}
              className="gap-1.5"
              onClick={handleFetch}
              disabled={loading || importing}
            >
              {loading ? <Loader2 size={13} className="animate-spin" /> : <Network size={13} />}
              {devices.length ? 'Refresh network map' : 'Fetch network map'}
            </Button>
          </div>

          {loading && (
            <p className="text-[11px] text-muted-foreground italic">
              Z2M can take a few minutes on large meshes — the coordinator polls every router for routing tables.
            </p>
          )}

          {devices.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={checked.size === devices.length}
                  ref={(el) => {
                    if (el) el.indeterminate = checked.size > 0 && checked.size < devices.length
                  }}
                  onChange={toggleAll}
                  className="w-3 h-3 accent-[#00d4ff] cursor-pointer"
                  title="Select all"
                />
                <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Devices ({checked.size}/{devices.length} selected)
                </span>
              </div>

              {(Object.entries(groupedDevices) as [keyof typeof groupedDevices, ZigbeeNode[]][])
                .filter(([, group]) => group.length > 0)
                .map(([type, group]) => {
                  const Icon = DEVICE_TYPE_ICON[type]
                  const color = DEVICE_TYPE_COLOR[type]
                  return (
                    <div key={type}>
                      <div className="flex items-center gap-1.5 mb-1">
                        <Icon size={11} style={{ color }} />
                        <span
                          className="text-[10px] font-medium uppercase tracking-wider"
                          style={{ color }}
                        >
                          {DEVICE_TYPE_LABEL[type]} ({group.length})
                        </span>
                      </div>
                      {group.map((device) => (
                        <div
                          key={device.id}
                          className={`flex items-start gap-2 p-2 mb-1 rounded-md text-xs cursor-pointer transition-colors border ${
                            checked.has(device.id)
                              ? 'bg-[#21262d] border-[#00d4ff]/40'
                              : 'bg-[#21262d] border-transparent hover:bg-[#30363d]'
                          }`}
                          onClick={() => toggleCheck(device.id)}
                        >
                          <input
                            type="checkbox"
                            checked={checked.has(device.id)}
                            onChange={() => toggleCheck(device.id)}
                            onClick={(e) => e.stopPropagation()}
                            className="w-3 h-3 mt-0.5 accent-[#00d4ff] cursor-pointer shrink-0"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="text-foreground font-medium truncate">
                              {device.friendly_name}
                            </div>
                            <div className="font-mono text-[10px] text-muted-foreground truncate">
                              {device.ieee_address}
                            </div>
                            {(device.model || device.vendor) && (
                              <div className="text-[10px] text-muted-foreground truncate">
                                {[device.vendor, device.model].filter(Boolean).join(' · ')}
                              </div>
                            )}
                          </div>
                          {device.lqi != null && (
                            <span
                              className="text-[9px] font-mono px-1 py-0.5 rounded border shrink-0"
                              style={{ color: '#8b949e', borderColor: '#8b949e40' }}
                            >
                              LQI {device.lqi}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )
                })}
            </div>
          )}
        </div>

        <DialogFooter className="gap-2 shrink-0 pt-2 border-t border-border">
          <Button variant="ghost" onClick={resetAndClose}>
            Cancel
          </Button>
          {devices.length > 0 && (
            <Button
              onClick={handleImport}
              disabled={checked.size === 0 || importing}
              style={{ background: '#00d4ff', color: '#0d1117' }}
              className="gap-1.5"
            >
              {importing ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
              Import {checked.size} to Pending
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
