import { describe, it, expect } from 'vitest'
import { buildZigbeeProperties, isZigbeeType } from '../zigbeeProperties'

describe('isZigbeeType', () => {
  it('matches the three zigbee node types', () => {
    expect(isZigbeeType('zigbee_coordinator')).toBe(true)
    expect(isZigbeeType('zigbee_router')).toBe(true)
    expect(isZigbeeType('zigbee_enddevice')).toBe(true)
  })

  it('rejects non-zigbee and empty values', () => {
    expect(isZigbeeType('generic')).toBe(false)
    expect(isZigbeeType('server')).toBe(false)
    expect(isZigbeeType(undefined)).toBe(false)
    expect(isZigbeeType(null)).toBe(false)
    expect(isZigbeeType('')).toBe(false)
  })
})

describe('buildZigbeeProperties', () => {
  it('includes only non-empty fields, all hidden by default', () => {
    const props = buildZigbeeProperties({
      ieee_address: '0xABCD',
      vendor: 'Aqara',
      model: null,
      lqi: 200,
    })
    expect(props.map((p) => p.key)).toEqual(['IEEE', 'Vendor', 'LQI'])
    expect(props.every((p) => p.visible === false)).toBe(true)
    expect(props.every((p) => p.icon === null)).toBe(true)
    expect(props.find((p) => p.key === 'LQI')?.value).toBe('200')
  })

  it('keeps lqi of 0 (only null/undefined are missing)', () => {
    const props = buildZigbeeProperties({ lqi: 0 })
    expect(props).toEqual([{ key: 'LQI', value: '0', icon: null, visible: false }])
  })

  it('returns empty array when nothing is set', () => {
    expect(buildZigbeeProperties({})).toEqual([])
  })

  it('matches the backend property shape', () => {
    const props = buildZigbeeProperties({
      ieee_address: '0x1',
      vendor: 'V',
      model: 'M',
      lqi: 50,
    })
    expect(props).toEqual([
      { key: 'IEEE', value: '0x1', icon: null, visible: false },
      { key: 'Vendor', value: 'V', icon: null, visible: false },
      { key: 'Model', value: 'M', icon: null, visible: false },
      { key: 'LQI', value: '50', icon: null, visible: false },
    ])
  })
})
