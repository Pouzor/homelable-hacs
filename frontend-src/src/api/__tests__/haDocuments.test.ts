/**
 * The documentation space talks to the integration through `homelable/docs/*`;
 * names and argument shapes must match
 * `custom_components/homelable/websocket_documents.py`.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const wsCall = vi.fn()
vi.mock('@/lib/hass', () => ({
  wsCall: (...args: unknown[]) => wsCall(...args),
  wsSubscribe: vi.fn(),
  getHass: vi.fn(),
}))

import { documentsApi } from '../ha'

beforeEach(() => {
  wsCall.mockReset()
  wsCall.mockResolvedValue({})
})

describe('documentsApi', () => {
  it('maps every call onto its WS command', async () => {
    await documentsApi.list({ kind: 'device' })
    expect(wsCall).toHaveBeenLastCalledWith('homelable/docs/list', { kind: 'device' })

    await documentsApi.get('d1')
    expect(wsCall).toHaveBeenLastCalledWith('homelable/docs/get', { document_id: 'd1' })

    await documentsApi.update('d1', { body: 'x', parent_id: null })
    expect(wsCall).toHaveBeenLastCalledWith('homelable/docs/update', {
      document_id: 'd1',
      body: 'x',
      parent_id: null,
    })

    await documentsApi.restore('d1', 'r1')
    expect(wsCall).toHaveBeenLastCalledWith('homelable/docs/restore', { document_id: 'd1', revision_id: 'r1' })

    await documentsApi.updateFromDevice('d1', 'p1', [{ id: 'c1', choice: 'device' }])
    expect(wsCall).toHaveBeenLastCalledWith('homelable/docs/update_from_device', {
      document_id: 'd1',
      preview_id: 'p1',
      resolutions: [{ id: 'c1', choice: 'device' }],
    })

    await documentsApi.block('services', 'dev-1')
    expect(wsCall).toHaveBeenLastCalledWith('homelable/docs/block', { block: 'services', device_id: 'dev-1' })

    await documentsApi.search('nas', 5)
    expect(wsCall).toHaveBeenLastCalledWith('homelable/docs/search', { q: 'nas', limit: 5 })
  })

  it('hands back the result as axios-like `.data`', async () => {
    wsCall.mockResolvedValue([{ id: 'd1' }])
    const res = await documentsApi.list()
    expect(res.data).toEqual([{ id: 'd1' }])
  })

  it('rethrows a refusal in the axios shape the store reads', async () => {
    wsCall.mockRejectedValue({ code: 'conflict', message: 'The document changed' })
    await expect(documentsApi.updateFromDevice('d1', 'p1')).rejects.toMatchObject({
      response: { status: 409, data: { detail: 'The document changed' } },
    })
  })

  it('maps an unknown error code to a 500', async () => {
    wsCall.mockRejectedValue({ code: 'weird', message: 'boom' })
    await expect(documentsApi.get('d1')).rejects.toMatchObject({ response: { status: 500 } })
  })

  it('decodes the base64 export into a zip blob with its server filename', async () => {
    wsCall.mockResolvedValue({ filename: 'homelable-documentation-20261116.zip', content: btoa('PK\u0003\u0004') })
    const res = await documentsApi.export()
    expect(res.data).toBeInstanceOf(Blob)
    expect(res.data.type).toBe('application/zip')
    expect(res.data.size).toBe(4)
    expect(res.headers['content-disposition']).toContain('homelable-documentation-20261116.zip')
  })
})
