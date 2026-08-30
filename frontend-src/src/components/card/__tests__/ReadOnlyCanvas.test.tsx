/**
 * The card canvas. React Flow is mocked down to a probe that records the props
 * it was handed — what matters here is that every editing affordance is off and
 * that a failed or empty load never shows demo data.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { HomelableCardConfig } from '@/lib/cardConfig'

const flowProps = vi.fn()
const fitView = vi.fn()

/** Captures the observer so a test can fire a resize by hand. */
let resizeCallback: (() => void) | null = null

class FakeResizeObserver {
  constructor(callback: () => void) {
    resizeCallback = callback
  }
  observe() {}
  disconnect() {
    resizeCallback = null
  }
}
vi.stubGlobal('ResizeObserver', FakeResizeObserver)

vi.mock('@xyflow/react', () => ({
  ReactFlow: (props: Record<string, unknown>) => {
    flowProps(props)
    return <div data-testid="flow">{(props.nodes as unknown[]).length} nodes</div>
  },
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Background: () => null,
  Controls: () => null,
  BackgroundVariant: { Dots: 'dots' },
  ConnectionMode: { Loose: 'loose' },
  useReactFlow: () => ({ fitView }),
}))

vi.mock('@/components/canvas/FloorMapLayer', () => ({ FloorMapLayer: () => null }))
vi.mock('@/components/canvas/nodes/nodeTypes', () => ({ nodeTypes: {} }))
vi.mock('@/components/canvas/edges/edgeTypes', () => ({ edgeTypes: {} }))
vi.mock('@/hooks/useStatusPolling', () => ({ useStatusPolling: vi.fn() }))

const load = vi.fn()
vi.mock('@/api/client', () => ({ canvasApi: { load: (id?: string) => load(id) } }))

vi.mock('@/lib/hass', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hass')>()),
  useHass: () => ({ connection: {}, themes: { darkMode: false } }),
}))

const { ReadOnlyCanvas } = await import('../ReadOnlyCanvas')
const { useCanvasStore } = await import('@/stores/canvasStore')
const { useThemeStore } = await import('@/stores/themeStore')

const CONFIG: HomelableCardConfig = {
  height: 400,
  fit_view: true,
  interactive: 'pan',
  open_on_click: false,
}

const PAYLOAD = {
  data: {
    nodes: [
      { id: 'n1', type: 'server', name: 'NAS', ip: '10.0.0.5', position_x: 0, position_y: 0 },
    ],
    edges: [],
    viewport: { theme_id: 'matrix' },
  },
}

describe('ReadOnlyCanvas', () => {
  beforeEach(() => {
    load.mockReset()
    flowProps.mockClear()
    fitView.mockClear()
    useCanvasStore.setState({ nodes: [], edges: [] })
    useThemeStore.setState({ activeTheme: 'default' })
  })

  it('loads the configured design and renders its nodes', async () => {
    load.mockResolvedValue(PAYLOAD)
    render(<ReadOnlyCanvas config={{ ...CONFIG, design_id: 'design-7' }} />)

    await screen.findByTestId('flow')
    expect(load).toHaveBeenCalledWith('design-7')
    expect(useCanvasStore.getState().nodes).toHaveLength(1)
  })

  it('loads the default design when none is configured', async () => {
    load.mockResolvedValue(PAYLOAD)
    render(<ReadOnlyCanvas config={CONFIG} />)
    await screen.findByTestId('flow')
    expect(load).toHaveBeenCalledWith(undefined)
  })

  it('applies the theme saved with the canvas', async () => {
    load.mockResolvedValue(PAYLOAD)
    render(<ReadOnlyCanvas config={CONFIG} />)
    await screen.findByTestId('flow')
    expect(useThemeStore.getState().activeTheme).toBe('matrix')
  })

  it("falls back to HA's dark theme when the canvas saved none", async () => {
    load.mockResolvedValue({ data: { ...PAYLOAD.data, viewport: {} } })
    render(<ReadOnlyCanvas config={CONFIG} />)
    await screen.findByTestId('flow')
    // useHass is mocked light, so the fallback is the default theme.
    expect(useThemeStore.getState().activeTheme).toBe('default')
  })

  it('disables every editing affordance', async () => {
    load.mockResolvedValue(PAYLOAD)
    render(<ReadOnlyCanvas config={CONFIG} />)
    await screen.findByTestId('flow')

    const props = flowProps.mock.calls.at(-1)![0]
    expect(props).toMatchObject({
      nodesDraggable: false,
      nodesConnectable: false,
      elementsSelectable: false,
      edgesReconnectable: false,
      zoomOnDoubleClick: false,
      deleteKeyCode: null,
    })
    expect(props.onNodesChange).toBeUndefined()
    expect(props.onConnect).toBeUndefined()
  })

  it('leaves wheel scrolling to the dashboard', async () => {
    load.mockResolvedValue(PAYLOAD)
    render(<ReadOnlyCanvas config={CONFIG} />)
    await screen.findByTestId('flow')

    const props = flowProps.mock.calls.at(-1)![0]
    expect(props.zoomOnScroll).toBe(false)
    expect(props.preventScrolling).toBe(false)
    expect(props.zoomActivationKeyCode).toBe('Control')
  })

  it('stops panning when interactive is none', async () => {
    load.mockResolvedValue(PAYLOAD)
    render(<ReadOnlyCanvas config={{ ...CONFIG, interactive: 'none' }} />)
    await screen.findByTestId('flow')
    expect(flowProps.mock.calls.at(-1)![0].panOnDrag).toBe(false)
  })

  it('shows an empty state instead of demo nodes for a canvas with no devices', async () => {
    load.mockResolvedValue({ data: { nodes: [], edges: [], viewport: {} } })
    render(<ReadOnlyCanvas config={CONFIG} />)

    await screen.findByText(/no devices yet/i)
    expect(screen.queryByTestId('flow')).toBeNull()
    expect(useCanvasStore.getState().nodes).toHaveLength(0)
  })

  it('shows an error state instead of demo nodes when the load fails', async () => {
    load.mockRejectedValue(new Error('ws down'))
    render(<ReadOnlyCanvas config={CONFIG} />)

    await screen.findByText(/canvas unavailable/i)
    expect(screen.queryByTestId('flow')).toBeNull()
    expect(useCanvasStore.getState().nodes).toHaveLength(0)
  })

  it('reloads when the configured design changes', async () => {
    load.mockResolvedValue(PAYLOAD)
    const { rerender } = render(<ReadOnlyCanvas config={{ ...CONFIG, design_id: 'a' }} />)
    await screen.findByTestId('flow')

    rerender(<ReadOnlyCanvas config={{ ...CONFIG, design_id: 'b' }} />)
    await waitFor(() => expect(load).toHaveBeenCalledWith('b'))
  })
})

describe('ReadOnlyCanvas fitting', () => {
  beforeEach(() => {
    load.mockReset()
    load.mockResolvedValue(PAYLOAD)
    flowProps.mockClear()
    fitView.mockClear()
    useCanvasStore.setState({ nodes: [], edges: [] })
  })

  it("leaves the initial fit to React Flow, which knows when it has measured itself", async () => {
    render(<ReadOnlyCanvas config={CONFIG} />)
    await screen.findByTestId('flow')

    const props = flowProps.mock.calls.at(-1)![0]
    expect(props.fitView).toBe(true)
    expect(props.fitViewOptions).toEqual({ padding: 0.12 })
  })

  it('does not ask React Flow to fit when fit_view is off', async () => {
    render(<ReadOnlyCanvas config={{ ...CONFIG, fit_view: false }} />)
    await screen.findByTestId('flow')
    expect(flowProps.mock.calls.at(-1)![0].fitView).toBe(false)
  })

  it('refits on the frame after a resize, not during it', async () => {
    render(<ReadOnlyCanvas config={CONFIG} />)
    await screen.findByTestId('flow')
    // Let the fit scheduled when the nodes landed run first.
    await waitFor(() => expect(fitView).toHaveBeenCalled())
    fitView.mockClear()

    resizeCallback!()
    // fitView reads the container size React Flow measured, which is stale
    // until the browser has laid the resize out.
    expect(fitView).not.toHaveBeenCalled()

    await waitFor(() => expect(fitView).toHaveBeenCalledWith({ padding: 0.12 }))
  })

  it('coalesces a burst of resizes into one fit', async () => {
    render(<ReadOnlyCanvas config={CONFIG} />)
    await screen.findByTestId('flow')
    await waitFor(() => expect(fitView).toHaveBeenCalled())
    fitView.mockClear()

    resizeCallback!()
    resizeCallback!()
    resizeCallback!()

    await waitFor(() => expect(fitView).toHaveBeenCalled())
    expect(fitView).toHaveBeenCalledTimes(1)
  })

  it('observes nothing when fit_view is off', async () => {
    render(<ReadOnlyCanvas config={{ ...CONFIG, fit_view: false }} />)
    await screen.findByTestId('flow')

    expect(resizeCallback).toBeNull()
    expect(fitView).not.toHaveBeenCalled()
  })
})
