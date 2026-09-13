import { useEffect, useRef, useState } from 'react'
import { useNavigate, useLocation, Navigate } from 'react-router-dom'
import { Eye, EyeOff, ShieldCheck } from 'lucide-react'
import { ThemeSelector } from '@/components/themes/ThemeSelector'
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
  const [showPassword, setShowPassword] = useState(false)
  const errorRef = useRef(null)

  useEffect(() => {
    if (error) errorRef.current?.focus()
  }, [error])

  if (role) return <Navigate to={location.state?.from || (role === 'admin' ? '/overview' : '/dashboard')} replace />

  function landing(r) {
    return r === 'admin' ? '/overview' : '/dashboard'
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
    <div className="login-page flex min-h-full">
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

          <div className="mb-6 flex justify-end"><ThemeSelector /></div>
          {!pending2fa ? (
            <>
              <h2 className="text-xl font-semibold text-foreground">Sign in</h2>
              <p className="mt-1 text-sm text-muted-foreground">Enter your credentials to access the panel.</p>
              <form onSubmit={submitCredentials} className="mt-6 space-y-4">
                <FormField label="Username" htmlFor="username">
                  <Input id="username" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
                </FormField>
                <FormField label="Password" htmlFor="password">
                  <div className="relative">
                    <Input id="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required className="pr-10" />
                    <button type="button" onClick={() => setShowPassword((v) => !v)} className="absolute right-1 top-1/2 -translate-y-1/2 rounded-btn p-2 text-muted-foreground hover:text-foreground" aria-label={showPassword ? 'Hide password' : 'Show password'}>
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </FormField>
                {error && <p ref={errorRef} tabIndex={-1} className="rounded-btn bg-danger/10 px-3 py-2 text-sm text-danger outline-none focus-visible:ring-2 focus-visible:ring-ring" role="alert">{error}</p>}
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
              <p className="mt-1 text-sm text-muted-foreground">Enter a 6-digit authenticator code or one of your recovery codes.</p>
              <form onSubmit={submit2fa} className="mt-6 space-y-4">
                <FormField label="Authentication or recovery code" htmlFor="code">
                  <Input
                    id="code"
                    autoFocus
                    autoComplete="one-time-code"
                    placeholder="123456 or ABCDE-FGHIJ"
                    className="text-center text-lg tracking-wider"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    required
                  />
                </FormField>
                {error && <p ref={errorRef} tabIndex={-1} className="rounded-btn bg-danger/10 px-3 py-2 text-sm text-danger outline-none focus-visible:ring-2 focus-visible:ring-ring" role="alert">{error}</p>}
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
