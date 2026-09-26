/**
 * The rack canvas talks to the integration through these WS commands; the
 * names and argument shapes must match `custom_components/homelable/websocket.py`.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const wsCall = vi.fn()
vi.mock('@/lib/hass', () => ({
  wsCall: (...args: unknown[]) => wsCall(...args),
  wsSubscribe: vi.fn(),
  getHass: vi.fn(),
}))

import { racksApi, scanApi } from '../ha'

beforeEach(() => {
  wsCall.mockReset()
  wsCall.mockResolvedValue({})
})

describe('racksApi', () => {
  it('loads a design rack state', async () => {
    wsCall.mockResolvedValue({ racks: [], devices: [], cables: [], viewport: {} })
    const res = await racksApi.load('d1')
    expect(wsCall).toHaveBeenCalledWith('homelable/racks/get', { design_id: 'd1' })
    expect(res.data.racks).toEqual([])
  })

  it('saves the full payload, design id included', async () => {
    const payload = { design_id: 'd1', racks: [], devices: [], cables: [], viewport: { x: 0, y: 0, zoom: 1 } }
    await racksApi.save(payload)
    expect(wsCall).toHaveBeenCalledWith('homelable/racks/save', payload)
  })

  it('reads the rack inventory', async () => {
    wsCall.mockResolvedValue({ items: [{ id: 'pd-1' }] })
    const res = await racksApi.inventory('d1')
    expect(wsCall).toHaveBeenCalledWith('homelable/racks/inventory', { design_id: 'd1' })
    expect(res.data.items).toHaveLength(1)
  })
})

describe('scanApi — hand-made inventory entries', () => {
  it('creates one with add_pending', async () => {
    wsCall.mockResolvedValue({ id: 'pd-9', hostname: 'Shelf' })
    const res = await scanApi.createPending({ hostname: 'Shelf', discovery_source: 'rack' })
    expect(wsCall).toHaveBeenCalledWith('homelable/scan/add_pending', { hostname: 'Shelf', discovery_source: 'rack' })
    expect(res.data.id).toBe('pd-9')
  })

  it('deletes one for good through ignore', async () => {
    await scanApi.deletePending('pd-9')
    expect(wsCall).toHaveBeenCalledWith('homelable/scan/ignore', { device_id: 'pd-9' })
  })
})
