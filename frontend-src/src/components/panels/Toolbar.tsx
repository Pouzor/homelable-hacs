import { useRef } from 'react'
import { Save, LayoutDashboard, Download, Palette, Undo2, Redo2, HelpCircle, Table2, FileDown, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo } from '@/components/ui/Logo'
import { useCanvasStore } from '@/stores/canvasStore'
import { useDesignStore } from '@/stores/designStore'
import { useRackStore } from '@/rack/store'
import { RackToolbarActions } from './RackToolbarActions'

interface ToolbarProps {
  onSave: () => void
  onAutoLayout: () => void
  onExport: () => void
  onChangeStyle: () => void
  onUndo: () => void
  onRedo: () => void
  onShortcuts: () => void
  onExportMd: () => void
  onExportYaml: () => void
  onImportYaml: (content: string) => void
}

export function Toolbar({ onSave, onAutoLayout, onExport, onChangeStyle, onUndo, onRedo, onShortcuts, onExportMd, onExportYaml, onImportYaml }: ToolbarProps) {
  const { hasUnsavedChanges: canvasDirty, past, future } = useCanvasStore()
  const isRack = useDesignStore((s) => s.activeDesignType) === 'rack'
  const rackDirty = useRackStore((s) => s.hasUnsavedChanges)
  const hasUnsavedChanges = isRack ? rackDirty : canvasDirty
  const fileInputRef = useRef<HTMLInputElement>(null)

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      const content = ev.target?.result
      if (typeof content === 'string') onImportYaml(content)
    }
    reader.readAsText(file)
    e.target.value = ''
  }

  return (
    <header className="flex items-center gap-2 px-4 py-2 border-b border-border bg-popover shrink-0">
      <Logo size={28} showText={true} />
      <div className="flex-1" />
      {/* Undo/redo are canvas history; the rack canvas has none yet. */}
      {!isRack && (
        <>
          <Button
            size="sm" variant="ghost"
            className="gap-1.5 text-muted-foreground hover:text-foreground disabled:opacity-30 cursor-pointer hover:bg-secondary"
            onClick={onUndo}
            disabled={past.length === 0}
            title="Undo (Ctrl+Z)"
          >
            <Undo2 size={14} />
          </Button>
          <Button
            size="sm" variant="ghost"
            className="gap-1.5 text-muted-foreground hover:text-foreground disabled:opacity-30 cursor-pointer hover:bg-secondary"
            onClick={onRedo}
            disabled={future.length === 0}
            title="Redo (Ctrl+Y)"
          >
            <Redo2 size={14} />
          </Button>
        </>
      )}
      <div className="w-px h-4 bg-border mx-1" />
      {isRack && <RackToolbarActions />}
      {!isRack && (
      <>
      <Button size="sm" variant="ghost" className="gap-1.5 text-muted-foreground hover:text-foreground cursor-pointer hover:bg-secondary" onClick={onAutoLayout}>
        <LayoutDashboard size={14} /> Auto Layout
      </Button>
      <Button size="sm" variant="ghost" className="gap-1.5 text-muted-foreground hover:text-foreground cursor-pointer hover:bg-secondary" onClick={onChangeStyle}>
        <Palette size={14} /> Style
      </Button>
      <Button size="sm" variant="ghost" className="gap-1.5 text-muted-foreground hover:text-foreground cursor-pointer hover:bg-secondary" onClick={() => fileInputRef.current?.click()} title="Import from YAML">
        <Upload size={14} /> Import
      </Button>
      <input
        ref={fileInputRef}
        type="file"
        accept=".yaml,.yml"
        className="hidden"
        onChange={handleFileChange}
      />
      <Button size="sm" variant="ghost" className="gap-1.5 text-muted-foreground hover:text-foreground cursor-pointer hover:bg-secondary" onClick={onExportYaml} title="Export canvas as YAML">
        <Download size={14} /> Export
      </Button>
      </>
      )}
      {/* PNG capture is DOM-based, so it works for both canvas kinds. */}
      <Button size="sm" variant="ghost" className="gap-1.5 text-muted-foreground hover:text-foreground cursor-pointer hover:bg-secondary" onClick={onExport} title="Download canvas as PNG">
        <FileDown size={14} /> PNG
      </Button>
      {!isRack && (
        <Button size="sm" variant="ghost" className="gap-1.5 text-muted-foreground hover:text-foreground cursor-pointer hover:bg-secondary" onClick={onExportMd} title="Copy inventory as Markdown table">
          <Table2 size={14} /> MD
        </Button>
      )}
      <Button size="sm" variant="ghost" className="gap-1.5 text-muted-foreground hover:text-foreground cursor-pointer hover:bg-secondary" onClick={onShortcuts} title="Keyboard shortcuts (?)">
        <HelpCircle size={14} />
      </Button>
      <Button
        size="sm"
        className="gap-1.5 relative cursor-pointer border border-transparent hover:border-white"
        style={{
          background: hasUnsavedChanges ? 'var(--primary)' : undefined,
          color: hasUnsavedChanges ? 'var(--primary-foreground)' : undefined,
        }}
        onClick={() => onSave()}
      >
        {hasUnsavedChanges && (
          <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-warning border border-popover" />
        )}
        <Save size={14} /> Save
      </Button>
    </header>
  )
}
