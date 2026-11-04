import { useEffect, useRef, useState } from 'react'
import { useNavigate, useLocation, Navigate } from 'react-router-dom'
import { Eye, EyeOff, ShieldCheck } from 'lucide-react'
import { useAuth } from '@/store/auth'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { APP_VERSION } from '@/config/constants'
import { useBranding } from '@/hooks/useBranding'
import { activeTheme } from '@/themes/runtime'

// m:ss for the rate-limit countdown on the submit button.
function clock(seconds) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

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
  // While the server rate-limits sign-in (429), submitting again only extends
  // the refusal: count down to the time it gave and keep the button disabled.
  const [retryAt, setRetryAt] = useState(null)
  const [now, setNow] = useState(() => Date.now())
  const errorRef = useRef(null)
  const waitSeconds = retryAt ? Math.max(0, Math.ceil((retryAt - now) / 1000)) : 0

  useEffect(() => {
    if (error) errorRef.current?.focus()
  }, [error])

  useEffect(() => {
    if (!retryAt) return undefined
    const timer = window.setInterval(() => {
      const current = Date.now()
      setNow(current)
      if (current >= retryAt) { setRetryAt(null); setError(null) }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [retryAt])

  if (role) return <Navigate to={location.state?.from || landing(role)} replace />

  function landing(r) {
    return r === 'admin' ? '/overview' : r === 'reseller' ? '/reseller' : '/dashboard'
  }

  function fail(err, fallback) {
    setError(err.message || fallback)
    const started = Date.now()
    setNow(started)
    setRetryAt(err.status === 429 && err.retryAfter ? started + err.retryAfter * 1000 : null)
  }

  function backToSignIn() {
    useAuth.setState({ pending2fa: null })
    setCode('')
    setError(null)
    setRetryAt(null)
  }

  async function submitCredentials(e) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await login(username, password)
      if (!res.needs2fa) navigate(landing(res.role), { replace: true })
    } catch (err) {
      fail(err, 'Login failed.')
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
      fail(err, 'Verification failed.')
      // A refusal with a reason (the hosting account was suspended or
      // removed) ends this sign-in: another code cannot succeed.
      if (err.accountUnavailable || err.status === 403) useAuth.setState({ pending2fa: null })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <main className="login-main">
        <div className="login-brand">
          {!brandingReady ? <span className="brand-loading-placeholder" aria-hidden="true" /> : logoUrl
            ? <img src={logoUrl} alt={panelName} className="login-custom-logo" />
            : <span className="boron-brand-logo"><img src={`/static/dist/brand/boron-logo-${activeTheme()?.id === 'classic' ? 'navy' : 'white'}.svg`} alt={panelName} /></span>}
        </div>

        <div className="login-card">
          {!pending2fa ? (
            <>
              <h1 className="login-title">Sign in</h1>
              <p className="login-lead">Enter your credentials to access the control panel.</p>
              <form onSubmit={submitCredentials} className="login-form">
                <FormField label="Username" htmlFor="username">
                  {/* The countdown belongs to the refused name; another login may still sign in. */}
                  <Input id="username" autoFocus autoComplete="username" value={username} onChange={(e) => { setUsername(e.target.value); if (retryAt) { setRetryAt(null); setError(null) } }} required />
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
                <Button type="submit" size="lg" className="w-full" loading={loading} disabled={loading || waitSeconds > 0}>
                  {loading ? 'Signing in…' : waitSeconds > 0 ? `Try again in ${clock(waitSeconds)}` : 'Sign in'}
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
                <Button type="submit" size="lg" className="w-full" loading={loading} disabled={loading || waitSeconds > 0}>
                  {waitSeconds > 0 ? `Try again in ${clock(waitSeconds)}` : 'Verify'}
                </Button>
                <Button type="button" variant="ghost" className="w-full" disabled={loading} onClick={backToSignIn}>
                  Back to sign in
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
