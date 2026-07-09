import { useState } from 'react'
import { useNavigate, useLocation, Navigate } from 'react-router-dom'
import { Loader2, ShieldCheck } from 'lucide-react'
import { useAuth } from '@/store/auth'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { APP_VERSION } from '@/config/constants'
import { useBranding } from '@/hooks/useBranding'

export default function Login() {
  const { login, verify2fa, role, pending2fa } = useAuth()
  const { panelName, logoUrl } = useBranding()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  if (role) return <Navigate to={location.state?.from || (role === 'admin' ? '/accounts' : '/dashboard')} replace />

  function landing(r) {
    return r === 'admin' ? '/accounts' : '/dashboard'
  }

  async function submitCredentials(e) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await login(username, password)
      if (!res.needs2fa) navigate(landing(res.role), { replace: true })
    } catch (err) {
      setError(err.message || 'Login failed.')
    } finally {
      setLoading(false)
    }
  }

  async function submit2fa(e) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await verify2fa(code)
      navigate(landing(res.role), { replace: true })
    } catch (err) {
      setError(err.message || 'Verification failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-full">
      {/* Brand panel (hidden on small screens) */}
      <div className="hidden w-1/2 flex-col justify-between bg-sidebar p-12 lg:flex">
        <div className="flex items-center gap-3">
          {logoUrl ? (
            <img src={logoUrl} alt={panelName} className="h-10 w-10 rounded-btn object-contain" />
          ) : (
            <div className="flex h-10 w-10 items-center justify-center rounded-btn bg-accent text-lg font-bold text-accent-foreground">
              {panelName.charAt(0).toUpperCase()}
            </div>
          )}
          <span className="text-xl font-semibold text-white">{panelName}</span>
        </div>
        <div className="space-y-4">
          <h1 className="text-3xl font-semibold leading-tight text-white">
            Managed hosting,
            <br />
            <span className="text-accent-400">fully in your control.</span>
          </h1>
          <p className="max-w-md text-sm text-sidebar-muted">
            Provision accounts, manage domains, databases, email, SSL and applications from a single fast control panel.
          </p>
        </div>
        <p className="text-xs text-gray-600">{panelName} {APP_VERSION}</p>
      </div>

      {/* Form panel */}
      <div className="flex flex-1 items-center justify-center bg-background p-6">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <div className="flex items-center gap-2.5">
              {logoUrl ? (
                <img src={logoUrl} alt={panelName} className="h-9 w-9 rounded-btn object-contain" />
              ) : (
                <div className="flex h-9 w-9 items-center justify-center rounded-btn bg-accent font-bold text-accent-foreground">
                  {panelName.charAt(0).toUpperCase()}
                </div>
              )}
              <span className="text-lg font-semibold text-foreground">{panelName}</span>
            </div>
          </div>

          {!pending2fa ? (
            <>
              <h2 className="text-xl font-semibold text-foreground">Sign in</h2>
              <p className="mt-1 text-sm text-muted-foreground">Enter your credentials to access the panel.</p>
              <form onSubmit={submitCredentials} className="mt-6 space-y-4">
                <FormField label="Username" htmlFor="username">
                  <Input id="username" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
                </FormField>
                <FormField label="Password" htmlFor="password">
                  <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
                </FormField>
                {error && <p className="rounded-btn bg-danger/10 px-3 py-2 text-sm text-danger">{error}</p>}
                <Button type="submit" className="w-full" loading={loading} disabled={loading}>
                  {loading ? 'Signing in…' : 'Sign in'}
                </Button>
              </form>
            </>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-5 w-5 text-accent" />
                <h2 className="text-xl font-semibold text-foreground">Two-factor authentication</h2>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">Enter the 6-digit code from your authenticator app.</p>
              <form onSubmit={submit2fa} className="mt-6 space-y-4">
                <FormField label="Authentication code" htmlFor="code">
                  <Input
                    id="code"
                    autoFocus
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    placeholder="123456"
                    className="text-center text-lg tracking-[0.5em]"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    required
                  />
                </FormField>
                {error && <p className="rounded-btn bg-danger/10 px-3 py-2 text-sm text-danger">{error}</p>}
                <Button type="submit" className="w-full" loading={loading} disabled={loading}>
                  Verify
                </Button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
