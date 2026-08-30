/**
 * The canvas inside the Lovelace card: the panel's nodes and edges, rendered
 * read-only over the HA WebSocket channel.
 *
 * Differences from the panel canvas, all deliberate:
 *   - every editing affordance is off (drag, connect, select, delete, keyboard)
 *   - the wheel doesn't zoom. A card lives in a scrolling dashboard; hijacking
 *     the wheel there traps the page. Ctrl+wheel and the zoom buttons still work.
 *   - an empty or failed load shows a message. It never falls back to demo data
 *     the way the panel does — fake devices on someone's dashboard would read as
 *     real ones.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  ConnectionMode,
  useReactFlow,
  type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { canvasApi } from '@/api/client'
import { useStatusPolling } from '@/hooks/useStatusPolling'
import { useCanvasStore } from '@/stores/canvasStore'
import { useThemeStore } from '@/stores/themeStore'
import { hydrateCanvasPayload } from '@/utils/canvasPayload'
import { THEMES } from '@/utils/themes'
import { nodeTypes } from '@/components/canvas/nodes/nodeTypes'
import { edgeTypes } from '@/components/canvas/edges/edgeTypes'
import { FloorMapLayer } from '@/components/canvas/FloorMapLayer'
import { useHass } from '@/lib/hass'
import type { HomelableCardConfig } from '@/lib/cardConfig'
import type { NodeData } from '@/types'

type LoadState = 'loading' | 'ready' | 'empty' | 'error'

const FIT_VIEW_OPTIONS = { padding: 0.12 }

interface ReadOnlyCanvasProps {
  config: HomelableCardConfig
}

function CanvasBody({ config }: ReadOnlyCanvasProps) {
  const hass = useHass()
  const nodes = useCanvasStore((s) => s.nodes)
  const edges = useCanvasStore((s) => s.edges)
  const loadCanvas = useCanvasStore((s) => s.loadCanvas)
  const setFloorMap = useCanvasStore((s) => s.setFloorMap)
  const setTheme = useThemeStore((s) => s.setTheme)
  const setCustomStyle = useThemeStore((s) => s.setCustomStyle)
  const activeTheme = useThemeStore((s) => s.activeTheme)
  const theme = THEMES[activeTheme]
  const { fitView } = useReactFlow()
  const [state, setState] = useState<LoadState>('loading')

  useStatusPolling()

  // HA's own theme is the fallback for a canvas that never picked one, so a
  // fresh design doesn't sit light-on-dark in a dark dashboard.
  const haDarkMode = hass.themes?.darkMode ?? false

  useEffect(() => {
    let cancelled = false
    // No reset to 'loading' here: the state starts there, and on a design
    // switch keeping the previous canvas up until the new one lands avoids a
    // flash of empty card.
    canvasApi
      .load(config.design_id)
      .then((res) => {
        if (cancelled) return
        const hydrated = hydrateCanvasPayload(res.data)
        if (!hydrated) {
          setState('empty')
          return
        }
        setTheme(hydrated.themeId ?? (haDarkMode ? 'dark' : 'default'))
        if (hydrated.customStyle) setCustomStyle(hydrated.customStyle)
        setFloorMap(hydrated.floorMap)
        loadCanvas(hydrated.nodes, hydrated.edges)
        setState('ready')
      })
      .catch(() => {
        if (!cancelled) setState('error')
      })
    return () => {
      cancelled = true
    }
  }, [config.design_id, haDarkMode, loadCanvas, setFloorMap, setTheme, setCustomStyle])

  const wrapperRef = useRef<HTMLDivElement>(null)

  /**
   * `fitView` works off the container size React Flow has measured, which is
   * still the old one during the frame a resize is reported in. Deferring to
   * the next frame is what makes it correct in a Sections view, where the card
   * only reaches its final width after the grid has laid itself out.
   */
  const refit = useCallback(() => {
    const frame = requestAnimationFrame(() => fitView(FIT_VIEW_OPTIONS))
    return () => cancelAnimationFrame(frame)
  }, [fitView])

  // Dashboards reflow on every viewport change, and a Sections card is resized
  // by the grid well after it mounts. The initial fit is React Flow's own
  // `fitView` prop, which runs once it has measured itself.
  useEffect(() => {
    if (!config.fit_view || state !== 'ready') return
    const wrapper = wrapperRef.current
    if (!wrapper || typeof ResizeObserver === 'undefined') return
    let cancelFrame: (() => void) | undefined
    const observer = new ResizeObserver(() => {
      cancelFrame?.()
      cancelFrame = refit()
    })
    observer.observe(wrapper)
    return () => {
      cancelFrame?.()
      observer.disconnect()
    }
  }, [config.fit_view, state, refit])

  // Switching design replaces the nodes under a canvas that is already mounted,
  // so the `fitView` prop won't fire again.
  useEffect(() => {
    if (!config.fit_view || state !== 'ready' || nodes.length === 0) return
    return refit()
  }, [config.fit_view, state, nodes.length, refit])

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node<NodeData>) => {
      if (!config.open_on_click) return
      const ip = node.data.ip
      if (ip) window.open(`http://${ip}`, '_blank', 'noopener,noreferrer')
    },
    [config.open_on_click]
  )

  const panOnDrag = config.interactive !== 'none'
  const style = useMemo(
    () => ({ background: theme.colors.canvasBackground }),
    [theme.colors.canvasBackground]
  )

  if (state !== 'ready') {
    return (
      <Message>
        {state === 'loading'
          ? 'Loading canvas…'
          : state === 'empty'
            ? 'This design has no devices yet. Open the Homelable panel to build it.'
            : 'Canvas unavailable. Is the Homelable integration still set up?'}
      </Message>
    )
  }

  return (
    <div ref={wrapperRef} className="w-full h-full" style={style}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={onNodeClick}
        // React Flow fits once it has measured itself, which a timer of ours
        // cannot reliably wait for; the observer above handles later resizes.
        fitView={config.fit_view}
        fitViewOptions={FIT_VIEW_OPTIONS}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        edgesReconnectable={false}
        elementsSelectable={false}
        deleteKeyCode={null}
        selectionKeyCode={null}
        multiSelectionKeyCode={null}
        zoomOnDoubleClick={false}
        // Plain wheel scrolls the dashboard; Ctrl+wheel zooms the canvas.
        zoomOnScroll={false}
        zoomActivationKeyCode="Control"
        preventScrolling={false}
        panOnDrag={panOnDrag}
        minZoom={0.25}
        maxZoom={2.5}
        colorMode={theme.colors.reactFlowColorMode}
        connectionMode={ConnectionMode.Loose}
        proOptions={{ hideAttribution: false }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={16}
          size={1}
          color={theme.colors.canvasDotColor}
        />
        <FloorMapLayer />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}

function Message({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        padding: '16px',
        textAlign: 'center',
        fontSize: '0.875rem',
        color: 'var(--secondary-text-color, #727272)',
      }}
    >
      {children}
    </div>
  )
}

export function ReadOnlyCanvas({ config }: ReadOnlyCanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasBody config={config} />
    </ReactFlowProvider>
  )
}
