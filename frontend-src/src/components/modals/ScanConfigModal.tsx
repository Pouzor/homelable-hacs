import { useState, useEffect } from 'react'
import { Settings } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { scanApi } from '@/api/client'
import { toast } from 'sonner'

interface ScanConfigModalProps {
  open: boolean
  onClose: () => void
  onScanNow: () => void
}

export function ScanConfigModal({ open, onClose, onScanNow }: ScanConfigModalProps) {
  const [ranges, setRanges] = useState<string[]>([])
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open) return
    scanApi.getConfig()
      .then((res) => setRanges(res.data.ranges))
      .catch(() => setRanges([]))
  }, [open])

  const handleScanNow = async () => {
    if (ranges.length === 0) {
      toast.error('No IP ranges configured — set them in the integration options')
      return
    }
    setSaving(true)
    try {
      const res = await scanApi.trigger()
      if (res.data?.status === 'already_running') {
        toast.message('A scan is already running')
      } else {
        toast.success('Scan started — track progress in History')
      }
      onScanNow()
      onClose()
    } catch {
      toast.error('Failed to start scan')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-[#161b22] border-border max-w-md">
        <DialogHeader>
          <DialogTitle className="text-foreground">Scan Configuration</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label className="text-sm text-muted-foreground">IP Ranges (CIDR)</Label>
            {ranges.length === 0 ? (
              <p className="text-sm text-muted-foreground italic">
                No ranges configured.
              </p>
            ) : (
              ranges.map((r, i) => (
                <Input
                  key={i}
                  value={r}
                  readOnly
                  disabled
                  className="font-mono text-sm bg-[#0d1117] border-border"
                />
              ))
            )}
          </div>

          <p className="text-xs text-muted-foreground flex items-start gap-1.5">
            <Settings size={11} className="mt-0.5 shrink-0" />
            <span>
              Ranges are managed in <strong>Settings → Devices &amp; services → Homelable → Configure</strong>.
              Status check interval is configured there too.
            </span>
          </p>
        </div>

        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            onClick={handleScanNow}
            disabled={saving || ranges.length === 0}
            style={{ background: '#00d4ff', color: '#0d1117' }}
          >
            Scan Now
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
