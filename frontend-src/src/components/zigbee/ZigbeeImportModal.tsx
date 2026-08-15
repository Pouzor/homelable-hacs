import { useEffect, useState } from 'react'
import { Network, Loader2, ScanLine } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { zigbeeApi } from '@/api/ha'
import type { ZigbeeBackend, ZigbeeGateway } from '@/components/zigbee/types'
import { toast } from 'sonner'

/**
 * Zigbee import modal — HA build.
 *
 * Reads the mesh from whichever gateway the integration options point at:
 *  - ZHA: straight out of the running integration, no broker, real neighbour
 *    tables (so routers get their actual children and LQI).
 *  - Zigbee2MQTT: a networkmap round-trip over HA's MQTT integration.
 *
 * There is deliberately no gateway picker here. Which gateway you run is a
 * property of your setup, not a per-import decision, so it lives in the
 * integration options; this modal only names what it is about to use. Sniffing
 * for it would be worse than asking — a loaded MQTT integration says nothing
 * about Zigbee2MQTT actually running.
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
  const [gateway, setGateway] = useState<ZigbeeGateway | null>(null)

  // Ask on open rather than caching: the user can change the setting in the
  // integration options between two imports.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    zigbeeApi
      .gateway()
      .then(({ data }) => {
        if (!cancelled) setGateway(data)
      })
      .catch(() => {
        if (!cancelled) setGateway(null)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const active = gateway?.resolved ?? null

  const handleStart = async () => {
    setStarting(true)
    try {
      // No backend argument — the integration options are the authority.
      await zigbeeApi.startImport()
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

          <p className="text-xs text-muted-foreground">
            Gateway:{' '}
            <span className="text-foreground font-medium">
              {active ? BACKEND_LABEL[active] : 'checking…'}
            </span>
            {gateway?.source === 'auto' && active && ' (auto-detected)'}
          </p>

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

          {active && (
            <p className="text-[11px] text-muted-foreground">
              Running the other one? Change <strong className="text-foreground">Zigbee gateway</strong>{' '}
              in Settings → Devices &amp; services → Homelable → Configure.
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
