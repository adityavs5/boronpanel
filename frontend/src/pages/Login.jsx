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
  const { brandingReady, panelName, logoUrl } = useBranding()
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

  if (role) return <Navigate to={location.state?.from || landing(role)} replace />

  function landing(r) {
    return r === 'admin' ? '/overview' : r === 'reseller' ? '/reseller' : '/dashboard'
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
    <div className="login-page">
      <div className="login-theme"><ThemeSelector /></div>
      <main className="login-main">
        <div className="login-brand">
          {!brandingReady ? <span className="brand-loading-placeholder" aria-hidden="true" /> : logoUrl
            ? <img src={logoUrl} alt={panelName} className="login-custom-logo" />
            : <span className="boron-brand-logo"><img src="/static/dist/brand/boron-logo-white.svg" alt={panelName} /></span>}
        </div>

        <div className="login-card">
          {!pending2fa ? (
            <>
              <h1 className="login-title">Sign in</h1>
              <p className="login-lead">Enter your credentials to access the control panel.</p>
              <form onSubmit={submitCredentials} className="login-form">
                <FormField label="Username" htmlFor="username">
                  <Input id="username" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
                </FormField>
                <FormField label="Password" htmlFor="password">
                  <div className="relative">
                    <Input id="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required className="pr-10" />
                    <button type="button" onClick={() => setShowPassword((v) => !v)} className="login-reveal" aria-label={showPassword ? 'Hide password' : 'Show password'}>
                      {showPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                    </button>
                  </div>
                </FormField>
                {error && <p ref={errorRef} tabIndex={-1} className="login-error" role="alert">{error}</p>}
                <Button type="submit" size="lg" className="w-full" loading={loading} disabled={loading}>
                  {loading ? 'Signing in…' : 'Sign in'}
                </Button>
              </form>
            </>
          ) : (
            <>
              <span className="login-chip" aria-hidden="true"><ShieldCheck /></span>
              <h1 className="login-title">Two-factor authentication</h1>
              <p className="login-lead">Enter a 6-digit authenticator code or one of your recovery codes.</p>
              <form onSubmit={submit2fa} className="login-form">
                <FormField label="Authentication or recovery code" htmlFor="code">
                  <Input
                    id="code"
                    autoFocus
                    autoComplete="one-time-code"
                    placeholder="123456 or ABCDE-FGHIJ"
                    className="login-code"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    required
                  />
                </FormField>
                {error && <p ref={errorRef} tabIndex={-1} className="login-error" role="alert">{error}</p>}
                <Button type="submit" size="lg" className="w-full" loading={loading} disabled={loading}>
                  Verify
                </Button>
              </form>
            </>
          )}
        </div>
        <p className="login-version">{panelName} {APP_VERSION}</p>
      </main>
    </div>
  )
}
