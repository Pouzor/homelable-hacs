import { useState, useEffect } from 'react'
import { Settings, ChevronRight, ChevronDown } from 'lucide-react'
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

  // Deep-scan section. Options here are a per-scan override passed to trigger();
  // HACS does not persist deep-scan defaults (ranges are config-flow managed).
  const [deepOpen, setDeepOpen] = useState(false)
  const [httpProbe, setHttpProbe] = useState(false)
  const [verifyTls, setVerifyTls] = useState(false)
  const [httpRangesText, setHttpRangesText] = useState('')

  useEffect(() => {
    if (!open) return
    // Reset the per-scan deep-scan overrides each time the dialog opens.
    const resetDeep = () => {
      setDeepOpen(false)
      setHttpProbe(false)
      setVerifyTls(false)
      setHttpRangesText('')
    }
    scanApi.getConfig()
      .then((res) => setRanges(res.data.ranges))
      .catch(() => setRanges([]))
      .finally(resetDeep)
  }, [open])

  const parseHttpRanges = () =>
    httpRangesText.split(',').map((r) => r.trim()).filter(Boolean)

  const handleScanNow = async () => {
    if (ranges.length === 0) {
      toast.error('No IP ranges configured — set them in the integration options')
      return
    }
    setSaving(true)
    try {
      const res = await scanApi.trigger({
        http_ranges: parseHttpRanges(),
        http_probe_enabled: httpProbe,
        verify_tls: verifyTls,
      })
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

          {/* Deep Scan (opt-in, per-scan only) */}
          <div className="space-y-2 border-t border-border pt-3">
            <button
              type="button"
              onClick={() => setDeepOpen((v) => !v)}
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
            >
              {deepOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              Deep Scan
            </button>

            {deepOpen && (
              <div className="space-y-3 pl-1">
                <p className="text-xs text-muted-foreground">
                  Scan extra ports and probe HTTP services to identify apps on custom ports.
                  Applies to this scan only.
                </p>

                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">Extra port ranges</Label>
                  <Input
                    value={httpRangesText}
                    onChange={(e) => setHttpRangesText(e.target.value)}
                    placeholder="8000-8100, 9000-9100"
                    className="font-mono text-sm bg-[#0d1117] border-border"
                  />
                </div>

                <label className="flex items-center gap-2 text-sm text-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={httpProbe}
                    onChange={(e) => setHttpProbe(e.target.checked)}
                    className="accent-[#00d4ff]"
                  />
                  Enable HTTP probe
                </label>

                <label className="flex items-center gap-2 text-sm text-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={verifyTls}
                    onChange={(e) => setVerifyTls(e.target.checked)}
                    className="accent-[#00d4ff]"
                  />
                  Verify TLS certificates
                </label>
              </div>
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
