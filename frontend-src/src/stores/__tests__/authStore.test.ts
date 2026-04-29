import { describe, it, expect, beforeEach } from 'vitest'
import { useAuthStore } from '@/stores/authStore'

describe('authStore', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuthStore.setState({ token: null, isAuthenticated: false })
  })

  it('starts unauthenticated', () => {
    const { token, isAuthenticated } = useAuthStore.getState()
    expect(token).toBeNull()
    expect(isAuthenticated).toBe(false)
  })

  it('login sets token and isAuthenticated', () => {
    useAuthStore.getState().login('my-jwt-token')
    const { token, isAuthenticated } = useAuthStore.getState()
    expect(token).toBe('my-jwt-token')
    expect(isAuthenticated).toBe(true)
  })

  it('logout clears token and isAuthenticated', () => {
    useAuthStore.getState().login('my-jwt-token')
    useAuthStore.getState().logout()
    const { token, isAuthenticated } = useAuthStore.getState()
    expect(token).toBeNull()
    expect(isAuthenticated).toBe(false)
  })
})
