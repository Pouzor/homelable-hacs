import { describe, it, expect } from 'vitest'
import { buildZwaveProperties, isZwaveType } from '../zwaveProperties'

describe('isZwaveType', () => {
  it('matches the three zwave node types', () => {
    expect(isZwaveType('zwave_coordinator')).toBe(true)
    expect(isZwaveType('zwave_router')).toBe(true)
    expect(isZwaveType('zwave_enddevice')).toBe(true)
  })

  it('rejects non-zwave and empty values', () => {
    expect(isZwaveType('zigbee_coordinator')).toBe(false)
    expect(isZwaveType('generic')).toBe(false)
    expect(isZwaveType(undefined)).toBe(false)
    expect(isZwaveType(null)).toBe(false)
    expect(isZwaveType('')).toBe(false)
  })
})

describe('buildZwaveProperties', () => {
  it('includes only non-empty fields, all hidden by default', () => {
    const props = buildZwaveProperties({
      ieee_address: 'zwave-0x1-2',
      vendor: 'Fibaro',
      model: null,
    })
    expect(props.map((p) => p.key)).toEqual(['Z-Wave ID', 'Vendor'])
    expect(props.every((p) => p.visible === false)).toBe(true)
    expect(props.every((p) => p.icon === null)).toBe(true)
  })

  it('never emits an LQI row (Z-Wave has no LQI)', () => {
    const props = buildZwaveProperties({ ieee_address: 'zwave-0x1-2', vendor: 'V', model: 'M' })
    expect(props.some((p) => p.key === 'LQI')).toBe(false)
  })

  it('returns empty array when nothing is set', () => {
    expect(buildZwaveProperties({})).toEqual([])
  })

  it('matches the backend property shape', () => {
    const props = buildZwaveProperties({ ieee_address: 'zwave-0x1-2', vendor: 'V', model: 'M' })
    expect(props).toEqual([
      { key: 'Z-Wave ID', value: 'zwave-0x1-2', icon: null, visible: false },
      { key: 'Vendor', value: 'V', icon: null, visible: false },
      { key: 'Model', value: 'M', icon: null, visible: false },
    ])
  })
})
