import { useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { DESIGN_ICONS, DEFAULT_DESIGN_ICON, resolveDesignIcon } from '@/utils/designIcons'
import type { Design } from '@/types'

export interface DesignFormData {
  name: string
  icon: string
  /**
   * When set, create the new canvas by deep-copying this existing design instead
   * of starting blank. Only offered in create mode with `sourceDesigns` present.
   */
  sourceId?: string
}

interface DesignModalProps {
  open: boolean
  onClose: () => void
  onSubmit: (data: DesignFormData) => void
  initial?: DesignFormData
  title?: string
  submitLabel?: string
  /**
   * Existing designs offered as a copy source (create mode only). When non-empty,
   * a "Copy from existing" option appears; choosing it clones the picked canvas.
   */
  sourceDesigns?: Design[]
}

export function DesignModal({ open, onClose, onSubmit, initial, title = 'New Canvas', submitLabel = 'Create', sourceDesigns = [] }: DesignModalProps) {
  const [name, setName] = useState(initial?.name ?? '')
  const [icon, setIcon] = useState(initial?.icon ?? DEFAULT_DESIGN_ICON)

  // "Copy from existing" is create-mode only.
  const canCopy = sourceDesigns.length > 0
  const [fromExisting, setFromExisting] = useState(false)
  const [sourceId, setSourceId] = useState<string>(sourceDesigns[0]?.id ?? '')

  const handleSubmit = () => {
    const trimmed = name.trim()
    if (!trimmed) return
    if (canCopy && fromExisting && !sourceId) return
    const data: DesignFormData = { name: trimmed, icon }
    if (canCopy && fromExisting && sourceId) {
      data.sourceId = sourceId
    }
    onSubmit(data)
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="design-name">Name</Label>
            <Input
              id="design-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit() }}
              placeholder="e.g. Home Network, Rack Power"
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label>Icon</Label>
            <div className="grid grid-cols-8 gap-1.5">
              {DESIGN_ICONS.map((entry) => {
                const Icon = entry.icon
                const selected = entry.key === icon
                return (
                  <button
                    key={entry.key}
                    type="button"
                    aria-label={entry.label}
                    aria-pressed={selected}
                    title={entry.label}
                    onClick={() => setIcon(entry.key)}
                    className={`flex items-center justify-center aspect-square rounded-md border transition-colors cursor-pointer ${
                      selected
                        ? 'border-[#00d4ff] bg-[#00d4ff]/10 text-[#00d4ff]'
                        : 'border-border text-muted-foreground hover:text-foreground hover:border-[#30363d]'
                    }`}
                  >
                    <Icon size={16} />
                  </button>
                )
              })}
            </div>
          </div>

          {canCopy && (
            <div className="space-y-2 pt-2 border-t border-border">
              <div className="grid grid-cols-2 gap-1.5">
                <button
                  type="button"
                  aria-pressed={!fromExisting}
                  onClick={() => setFromExisting(false)}
                  className={`text-xs rounded-md border py-2 transition-colors cursor-pointer ${
                    !fromExisting
                      ? 'border-[#00d4ff] bg-[#00d4ff]/10 text-[#00d4ff]'
                      : 'border-border text-muted-foreground hover:text-foreground'
                  }`}
                >
                  Blank canvas
                </button>
                <button
                  type="button"
                  aria-pressed={fromExisting}
                  onClick={() => setFromExisting(true)}
                  className={`text-xs rounded-md border py-2 transition-colors cursor-pointer ${
                    fromExisting
                      ? 'border-[#00d4ff] bg-[#00d4ff]/10 text-[#00d4ff]'
                      : 'border-border text-muted-foreground hover:text-foreground'
                  }`}
                >
                  Copy from existing
                </button>
              </div>

              {fromExisting && (
                <div className="space-y-1 max-h-48 overflow-y-auto pr-1" role="radiogroup" aria-label="Source canvas">
                  {sourceDesigns.map((d) => {
                    const Icon = resolveDesignIcon(d.icon)
                    const selected = d.id === sourceId
                    return (
                      <button
                        key={d.id}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        onClick={() => setSourceId(d.id)}
                        className={`w-full flex items-center gap-2.5 rounded-md border px-2.5 py-2 text-left transition-colors cursor-pointer ${
                          selected
                            ? 'border-[#00d4ff] bg-[#00d4ff]/10'
                            : 'border-border hover:border-[#30363d]'
                        }`}
                      >
                        <Icon size={16} className={selected ? 'text-[#00d4ff]' : 'text-muted-foreground'} />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm">{d.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {d.node_count ?? 0} nodes · {d.group_count ?? 0} groups · {d.text_count ?? 0} text
                          </div>
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={!name.trim()}>{submitLabel}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
