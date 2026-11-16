import { describe, it, expect } from 'vitest'
import type { PendingDevice } from '@/types'
import { deviceLastSeen, ipv4ToNumber, nextSort, sortDevices } from '@/utils/inventorySort'

function dev(id: string, over: Partial<PendingDevice> = {}): PendingDevice {
  return {
    id, ip: null, mac: null, hostname: id, os: null, services: [],
    suggested_type: null, status: 'pending', discovery_source: 'tcp',
    discovered_at: '2020-01-01T00:00:00Z',
    ...over,
  }
}

const ids = (ds: PendingDevice[]) => ds.map((d) => d.id)

describe('ipv4ToNumber', () => {
  it('orders octets numerically', () => {
    expect(ipv4ToNumber('10.0.0.9')!).toBeLessThan(ipv4ToNumber('10.0.0.10')!)
    expect(ipv4ToNumber('9.255.255.255')!).toBeLessThan(ipv4ToNumber('10.0.0.0')!)
  })

  it('rejects anything that is not a dotted quad', () => {
    expect(ipv4ToNumber(null)).toBeNull()
    expect(ipv4ToNumber('fe80::1')).toBeNull()
    expect(ipv4ToNumber('10.0.0')).toBeNull()
    expect(ipv4ToNumber('10.0.0.256')).toBeNull()
  })
})

describe('sortDevices', () => {
  it('sorts IPs numerically, not as strings', () => {
    const list = [dev('a', { ip: '10.0.0.10' }), dev('b', { ip: '10.0.0.9' }), dev('c', { ip: '10.0.0.100' })]
    expect(ids(sortDevices(list, { key: 'ip', dir: 'asc' }))).toEqual(['b', 'a', 'c'])
    expect(ids(sortDevices(list, { key: 'ip', dir: 'desc' }))).toEqual(['c', 'a', 'b'])
  })

  it('keeps devices without a value at the bottom in both directions', () => {
    const list = [dev('none'), dev('x', { ip: '10.0.0.2' }), dev('y', { ip: '10.0.0.1' })]
    expect(ids(sortDevices(list, { key: 'ip', dir: 'asc' }))).toEqual(['y', 'x', 'none'])
    expect(ids(sortDevices(list, { key: 'ip', dir: 'desc' }))).toEqual(['x', 'y', 'none'])
  })

  it('sorts by display name, case-insensitively, preferring the curated label', () => {
    const list = [dev('h1', { hostname: 'zeta' }), dev('h2', { hostname: 'alpha', label: 'Mid' }), dev('h3', { hostname: 'Beta' })]
    expect(ids(sortDevices(list, { key: 'name', dir: 'asc' }))).toEqual(['h3', 'h2', 'h1'])
  })

  it('sorts counts and breaks ties by name', () => {
    const list = [
      dev('b', { canvas_count: 1 }),
      dev('a', { canvas_count: 1 }),
      dev('c', { canvas_count: 3 }),
    ]
    expect(ids(sortDevices(list, { key: 'canvas', dir: 'desc' }))).toEqual(['c', 'a', 'b'])
  })

  it('sorts by last seen, falling back to discovery time', () => {
    const list = [
      dev('old', { discovered_at: '2021-01-01T00:00:00Z' }),
      dev('seen', { discovered_at: '2020-01-01T00:00:00Z', last_seen: '2024-06-01T00:00:00Z' }),
    ]
    expect(ids(sortDevices(list, { key: 'seen', dir: 'desc' }))).toEqual(['seen', 'old'])
    expect(deviceLastSeen(list[0])).toBe('2021-01-01T00:00:00Z')
  })

  it('does not mutate its input', () => {
    const list = [dev('b', { ip: '10.0.0.2' }), dev('a', { ip: '10.0.0.1' })]
    sortDevices(list, { key: 'ip', dir: 'asc' })
    expect(ids(list)).toEqual(['b', 'a'])
  })
})

describe('nextSort', () => {
  it('flips direction on the active column', () => {
    expect(nextSort({ key: 'ip', dir: 'asc' }, 'ip')).toEqual({ key: 'ip', dir: 'desc' })
    expect(nextSort({ key: 'ip', dir: 'desc' }, 'ip')).toEqual({ key: 'ip', dir: 'asc' })
  })

  it('starts text columns ascending and date / count columns descending', () => {
    expect(nextSort({ key: 'ip', dir: 'desc' }, 'name')).toEqual({ key: 'name', dir: 'asc' })
    expect(nextSort({ key: 'name', dir: 'asc' }, 'seen')).toEqual({ key: 'seen', dir: 'desc' })
    expect(nextSort({ key: 'name', dir: 'asc' }, 'canvas')).toEqual({ key: 'canvas', dir: 'desc' })
  })
})
