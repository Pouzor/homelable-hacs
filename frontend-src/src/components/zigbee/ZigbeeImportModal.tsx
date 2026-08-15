import { useEffect, useState } from 'react'
import { Network, Loader2, ScanLine } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { zigbeeApi } from '@/api/ha'
import type { ZigbeeBackend, ZigbeeBackends } from '@/components/zigbee/types'
import { toast } from 'sonner'

/**
 * Zigbee import modal — HA build.
 *
 * Reads the mesh from whichever gateway the user runs:
 *  - ZHA: straight out of the running integration, no broker, real neighbour
 *    tables (so routers get their actual children and LQI).
 *  - Zigbee2MQTT: a networkmap round-trip over HA's MQTT integration.
 *
 * Differs from the standalone modal:
 *  - No MQTT host/port/user/pass/TLS form. HA's MQTT integration owns the
 *    broker; the Z2M base topic is set in the options flow.
 *  - No "send to canvas" direct mode. Discovered devices are pushed to the
 *    pending devices store; the user approves them from there.
 *  - The fetch runs as a background scan run: starting it closes this modal
 *    and opens Scan History so progress is visible (running → done),
 *    mirroring the IP "Scan Now" flow.
 */

interface ZigbeeImportModalProps {
  open: boolean
  onClose: () => void
  /** Called once the background import has been started (opens Scan History). */
  onImported?: () => void
}

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

const BACKEND_LABEL: Record<ZigbeeBackend, string> = {
  zha: 'ZHA',
  z2m: 'Zigbee2MQTT',
}

export function ZigbeeImportModal({ open, onClose, onImported }: ZigbeeImportModalProps) {
  const [starting, setStarting] = useState(false)
  const [backends, setBackends] = useState<ZigbeeBackends | null>(null)
  const [backend, setBackend] = useState<ZigbeeBackend | null>(null)

  // Probe on open so the picker below only appears for the rare setup that
  // runs both gateways; everyone else just gets the one they have.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    zigbeeApi
      .backends()
      .then(({ data }) => {
        if (cancelled) return
        setBackends(data)
        setBackend(data.default)
      })
      .catch(() => {
        if (!cancelled) setBackends(null)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const bothAvailable = Boolean(backends?.zha && backends?.z2m)
  // Until the probe answers (or if it fails) neither gateway is claimed —
  // showing Z2M copy to a ZHA-only user was the whole complaint.
  const active: ZigbeeBackend | null = backend ?? backends?.default ?? null

  const handleStart = async () => {
    setStarting(true)
    try {
      await zigbeeApi.startImport(backend ?? undefined)
      toast.success('Zigbee scan started — check Scan History for results')
      onImported?.()
      onClose()
    } catch (err) {
      const { message } = extractWsError(err)
      toast.error(message ?? 'Failed to start Zigbee scan')
    } finally {
      setStarting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-[#161b22] border-border max-w-md flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <Network size={16} className="text-[#00d4ff]" />
            Zigbee Import
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <p className="text-xs text-muted-foreground">
            Reads your Zigbee mesh and adds every device to Pending, where you approve
            them onto the canvas. Works with <strong className="text-foreground">ZHA</strong>{' '}
            or <strong className="text-foreground">Zigbee2MQTT</strong>.
          </p>

          {bothAvailable ? (
            <div className="space-y-1.5">
              <p className="text-xs text-muted-foreground">Read the mesh from</p>
              <div className="flex gap-2">
                {(['zha', 'z2m'] as const).map((b) => (
                  <Button
                    key={b}
                    type="button"
                    variant={active === b ? 'default' : 'ghost'}
                    onClick={() => setBackend(b)}
                    disabled={starting}
                    className="flex-1 text-xs"
                    style={active === b ? { background: '#00d4ff', color: '#0d1117' } : undefined}
                  >
                    {BACKEND_LABEL[b]}
                  </Button>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              Detected gateway:{' '}
              <span className="text-foreground font-medium">
                {active ? BACKEND_LABEL[active] : 'checking…'}
              </span>
            </p>
          )}

          {active === 'zha' && (
            <p className="text-[11px] text-muted-foreground italic">
              ZHA is read straight from the integration — no MQTT broker, no
              re-pairing, and it returns almost instantly. Routers, end devices and
              LQI come from the radio's neighbour tables. Results show in Scan
              History.
            </p>
          )}
          {active === 'z2m' && (
            <p className="text-[11px] text-muted-foreground italic">
              Zigbee2MQTT is fetched over the MQTT broker HA already uses (base topic
              set in the integration options). The scan runs in the background — it
              can take a few minutes on large meshes as the coordinator polls every
              router. Progress shows in Scan History; you can keep working meanwhile.
            </p>
          )}
          {active === null && (
            <p className="text-[11px] text-muted-foreground italic">
              Homelable picks the gateway for you: ZHA when its integration is set up,
              otherwise Zigbee2MQTT. Nothing to configure for ZHA.
            </p>
          )}
        </div>

        <DialogFooter className="gap-2 shrink-0 pt-2 border-t border-border">
          <Button variant="ghost" onClick={onClose} disabled={starting}>
            Cancel
          </Button>
          <Button
            onClick={handleStart}
            disabled={starting}
            style={{ background: '#00d4ff', color: '#0d1117' }}
            className="gap-1.5"
          >
            {starting ? <Loader2 size={13} className="animate-spin" /> : <ScanLine size={13} />}
            Start Zigbee scan
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
