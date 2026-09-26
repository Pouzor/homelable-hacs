/**
 * Device Inventory edits go to `homelable/scan/update_pending` (port of
 * homelable #339). The curated `type` must travel as `node_type`: on the HA
 * socket, `type` is the command name.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const wsCall = vi.fn()
vi.mock('@/lib/hass', () => ({
  wsCall: (...args: unknown[]) => wsCall(...args),
  wsSubscribe: vi.fn(),
  getHass: vi.fn(),
}))

import { scanApi } from '../ha'

beforeEach(() => {
  wsCall.mockReset()
  wsCall.mockResolvedValue({ id: 'pd-1' })
})

describe('scanApi — inventory edits', () => {
  it('updatePending sends a partial edit with the type as node_type', async () => {
    await scanApi.updatePending('pd-1', { label: 'NAS', type: 'nas', notes: null })
    expect(wsCall).toHaveBeenCalledWith('homelable/scan/update_pending', {
      device_id: 'pd-1',
      label: 'NAS',
      node_type: 'nas',
      notes: null,
    })
  })

  it('updatePending leaves node_type out when the type is not edited', async () => {
    await scanApi.updatePending('pd-1', { notes: 'loft' })
    expect(wsCall.mock.calls[0][1]).toEqual({ device_id: 'pd-1', notes: 'loft' })
  })

  it('createPending carries curated fields the same way', async () => {
    await scanApi.createPending({ hostname: 'ups', type: 'ups', check_method: 'none' })
    expect(wsCall).toHaveBeenCalledWith('homelable/scan/add_pending', {
      hostname: 'ups',
      node_type: 'ups',
      check_method: 'none',
    })
  })
})
