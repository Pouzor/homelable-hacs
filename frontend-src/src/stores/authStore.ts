import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'

interface AuthState {
  token: string | null
  isAuthenticated: boolean
  login: (token: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      isAuthenticated: false,
      login: (token) => set({ token, isAuthenticated: true }),
      logout: () => set({ token: null, isAuthenticated: false }),
    }),
    {
      name: 'homelable-auth',
      // sessionStorage: scoped to the tab, cleared on browser close.
      // Prevents XSS from other tabs stealing the token via localStorage.
      storage: createJSONStorage(() => sessionStorage),
    }
  )
)
