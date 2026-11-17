/**
 * The canvas remembers its pan/zoom across a trip to Documentation, which
 * unmounts React Flow (homelable #422).
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useCanvasStore } from '../canvasStore'

beforeEach(() => {
  useCanvasStore.setState({ savedViewport: null })
})

describe('canvasStore — saved viewport', () => {
  it('keeps the last pan/zoom it was given', () => {
    useCanvasStore.getState().setSavedViewport({ x: 120, y: -40, zoom: 0.8 })
    expect(useCanvasStore.getState().savedViewport).toEqual({ x: 120, y: -40, zoom: 0.8 })
  })

  it('forgets it when another canvas loads', () => {
    useCanvasStore.getState().setSavedViewport({ x: 120, y: -40, zoom: 0.8 })
    useCanvasStore.getState().loadCanvas([], [])
    expect(useCanvasStore.getState().savedViewport).toBeNull()
  })
})
