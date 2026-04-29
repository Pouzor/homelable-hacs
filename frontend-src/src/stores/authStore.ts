/**
 * HA build: HA owns auth. Panel inherits the user's HA session via the `hass`
 * property. This stub preserves the original API surface so unmodified call
 * sites keep working — `isAuthenticated` is always true.
 */
import { create } from 'zustand'

interface AuthState {
  token: string | null
  isAuthenticated: boolean
  login: (token: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>(() => ({
  token: null,
  isAuthenticated: true,
  login: () => {},
  logout: () => {},
}))
