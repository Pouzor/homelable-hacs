import { describe, it, expect, vi, afterEach } from 'vitest'
import { generateUUID } from '../uuid'

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

describe('generateUUID', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('returns a valid v4 UUID using crypto.randomUUID when available', () => {
    const id = generateUUID()
    expect(id).toMatch(UUID_REGEX)
  })

  it('returns a valid v4 UUID using crypto.getRandomValues fallback', () => {
    vi.spyOn(crypto, 'randomUUID' as never).mockImplementation(undefined as never)
    const id = generateUUID()
    expect(id).toMatch(UUID_REGEX)
  })

  it('generates unique IDs', () => {
    const ids = new Set(Array.from({ length: 100 }, () => generateUUID()))
    expect(ids.size).toBe(100)
  })
})
