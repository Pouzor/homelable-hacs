import { useState } from 'react'
import { Network, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { authApi } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'

export function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const login = useAuthStore((s) => s.login)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await authApi.login(username, password)
      login(res.data.access_token)
    } catch (err: unknown) {
      const hasResponse = err && typeof err === 'object' && 'response' in err
      setError(hasResponse ? 'Invalid username or password' : 'Could not reach the server — check your CORS_ORIGINS setting')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-background">
      {/* Grid background */}
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: 'linear-gradient(#00d4ff 1px, transparent 1px), linear-gradient(90deg, #00d4ff 1px, transparent 1px)',
          backgroundSize: '48px 48px',
        }}
      />

      <div className="relative w-full max-w-sm px-4">
        {/* Logo */}
        <div className="flex flex-col items-center gap-3 mb-8">
          <div className="flex items-center justify-center w-14 h-14 rounded-xl bg-primary/10 border border-primary/20">
            <Network size={28} className="text-primary" />
          </div>
          <div className="text-center">
            <h1 className="text-xl font-semibold text-foreground tracking-wide">Homelable</h1>
            <p className="text-xs text-muted-foreground mt-0.5">HomeLab Visualizer</p>
          </div>
        </div>

        {/* Form */}
        <form
          onSubmit={handleSubmit}
          className="flex flex-col gap-4 bg-popover border border-border rounded-xl p-6"
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="username" className="text-xs text-muted-foreground">Username</Label>
            <Input
              id="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="bg-secondary border-border focus-visible:ring-primary/50 text-sm"
              placeholder="admin"
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="password" className="text-xs text-muted-foreground">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="bg-secondary border-border focus-visible:ring-primary/50 text-sm"
              required
            />
          </div>

          {error && (
            <p className="text-xs text-destructive text-center">{error}</p>
          )}

          <Button
            type="submit"
            disabled={loading}
            className="mt-1 bg-primary text-primary-foreground hover:bg-primary/90 font-medium"
          >
            {loading ? <Loader2 size={15} className="animate-spin" /> : 'Sign in'}
          </Button>
        </form>

        <p className="text-center text-[10px] text-muted-foreground/40 mt-4">
          Credentials configured in <span className="font-mono">.env</span>
        </p>
      </div>
    </div>
  )
}
