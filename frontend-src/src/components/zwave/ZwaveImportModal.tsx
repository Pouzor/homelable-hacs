import { useState } from 'react'
import { RadioTower, Loader2, ScanLine } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { zwaveApi } from '@/api/ha'
import { toast } from 'sonner'

/**
 * Z-Wave JS UI (zwavejs2mqtt) import modal — HA build.
 *
 * Mirrors the Zigbee import modal. Differs from the standalone modal:
 *  - No MQTT host/port/user/pass/TLS form. HA's MQTT integration owns the
 *    broker; the Z-Wave JS UI prefix + gateway name are set in the options flow.
 *  - No "send to canvas" direct mode. Discovered devices are pushed to the
 *    pending devices store; the user approves them from there.
 *  - The node-list fetch (a slow MQTT round-trip) runs as a background scan
 *    run: starting it closes this modal and opens Scan History so progress is
 *    visible (running → done), mirroring the IP "Scan Now" flow.
 */

interface ZwaveImportModalProps {
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

export function ZwaveImportModal({ open, onClose, onImported }: ZwaveImportModalProps) {
  const [starting, setStarting] = useState(false)

  const handleStart = async () => {
    setStarting(true)
    try {
      await zwaveApi.startImport()
      toast.success('Z-Wave scan started — check Scan History for results')
      onImported?.()
      onClose()
    } catch (err) {
      const { message } = extractWsError(err)
      toast.error(message ?? 'Failed to start Z-Wave scan')
    } finally {
      setStarting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-[#161b22] border-border max-w-md flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <RadioTower size={16} className="text-[#ff6e00]" />
            Z-Wave JS UI Import
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <p className="text-xs text-muted-foreground">
            Fetches the Z-Wave node list via Home Assistant's MQTT integration and
            adds discovered devices to Pending, where you can approve them onto
            the canvas.
          </p>
          <p className="text-[11px] text-muted-foreground italic">
            The scan runs in the background — Z-Wave JS UI can take a few minutes
            on large meshes as the gateway polls every node. Progress shows in
            Scan History; you can keep working meanwhile.
          </p>
        </div>

        <DialogFooter className="gap-2 shrink-0 pt-2 border-t border-border">
          <Button variant="ghost" onClick={onClose} disabled={starting}>
            Cancel
          </Button>
          <Button
            onClick={handleStart}
            disabled={starting}
            style={{ background: '#ff6e00', color: '#0d1117' }}
            className="gap-1.5"
          >
            {starting ? <Loader2 size={13} className="animate-spin" /> : <ScanLine size={13} />}
            Start Z-Wave scan
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
