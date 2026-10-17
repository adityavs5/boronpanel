import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ShieldCheck, ShieldOff, ShieldAlert, Copy, Download, Smartphone, MonitorSmartphone, LogOut, Save } from 'lucide-react'
import { del, get, patch, post } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { useAuth } from '@/store/auth'
import { copyToClipboard, formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import { Switch } from '@/components/ui/Toggle'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import { EmptyState, ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { toast } from '@/components/ui/Toast'

function ActiveSessions() {
  const qc = useQueryClient()
  const [revokeId, setRevokeId] = useState(null)
  const [revokeOthers, setRevokeOthers] = useState(false)
  const query = useQuery({ queryKey: ['active-sessions'], queryFn: () => get('/api/v1/security/sessions') })
  const revoke = useMutation({
    mutationFn: id => del(`/api/v1/security/sessions/${id}`),
    onSuccess: () => { setRevokeId(null); qc.invalidateQueries({ queryKey: ['active-sessions'] }); toast.success('Session revoked') },
    onError: error => toast.error('Could not revoke session', error.message),
  })
  const revokeAll = useMutation({
    mutationFn: () => post('/api/v1/security/sessions/revoke-others'),
    onSuccess: result => { setRevokeOthers(false); qc.invalidateQueries({ queryKey: ['active-sessions'] }); toast.success('Other sessions revoked', `${result.count} session${result.count === 1 ? '' : 's'} ended.`) },
    onError: error => toast.error('Could not revoke other sessions', error.message),
  })
  const rows = query.data?.sessions || []
  return <Card className="mt-6">
    <CardHeader><div><CardTitle>Active sessions</CardTitle><CardDescription>Review browsers signed in to this panel account and end access you do not recognize.</CardDescription></div></CardHeader>
    <CardContent>
      {query.isLoading ? <CardSkeleton className="border-0 p-0 shadow-none" /> : query.error ? <ErrorState error={query.error} onRetry={query.refetch} /> : <div className="divide-y divide-border">
        {rows.map(row => <div key={row.id} className="flex flex-wrap items-center gap-3 py-3">
          <MonitorSmartphone className="h-4 w-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-foreground" title={row.device}>{row.device}</div>
            <div className="text-xs text-muted-foreground">{row.ip_address || 'IP unavailable'} · Last seen {formatDate(row.last_seen_at)}</div>
          </div>
          {row.current ? <StatusBadge status="current" /> : <Button size="sm" variant="outline" onClick={() => setRevokeId(row.id)}><LogOut className="h-4 w-4" /> Revoke</Button>}
        </div>)}
        {!rows.length && <p className="py-4 text-sm text-muted-foreground">No active sessions were found.</p>}
      </div>}
      {!!query.data?.impersonation_sessions?.length && <div className="mt-5 border-t border-border pt-4">
        <h3 className="text-sm font-semibold text-foreground">Active impersonation sessions</h3>
        <p className="mt-1 text-xs text-muted-foreground">End an impersonation session without signing out your current administrator session.</p>
        {query.data.impersonation_sessions.map(row => <div key={row.id} className="mt-3 flex items-center gap-3 text-sm"><MonitorSmartphone className="h-4 w-4 text-muted-foreground" /><span className="min-w-0 flex-1 truncate">{row.device}</span><Button size="sm" variant="outline" disabled={!row.revocable} onClick={() => setRevokeId(row.id)}>Revoke session</Button></div>)}
      </div>}
    </CardContent>
    <CardFooter className="justify-end"><Button variant="outline" disabled={[...rows, ...(query.data?.impersonation_sessions || [])].filter(row => !row.current).length === 0} onClick={() => setRevokeOthers(true)}>Revoke all other sessions</Button></CardFooter>
    <ConfirmDialog open={revokeId != null} onOpenChange={open => !open && setRevokeId(null)} title="Revoke this session?" description="That browser will need to sign in again. Your current session stays active." confirmLabel="Revoke session" loading={revoke.isPending} onConfirm={() => revoke.mutate(revokeId)} />
    <ConfirmDialog open={revokeOthers} onOpenChange={setRevokeOthers} title="Revoke all other sessions?" description="Every other session, including impersonation sessions you started, will be signed out. Your current session stays active." confirmLabel="Revoke other sessions" loading={revokeAll.isPending} onConfirm={() => revokeAll.mutate()} />
  </Card>
}

function AdminTwoFactorPolicy() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['admin-2fa-policy'], queryFn: () => get('/api/v1/admin/security-policy') })
  const [form, setForm] = useState(null)
  useEffect(() => {
    if (query.data) setForm({ require_admin_2fa: query.data.require_admin_2fa, admin_2fa_grace_days: query.data.admin_2fa_grace_days })
  }, [query.data])
  const save = useMutation({
    mutationFn: body => patch('/api/v1/admin/security-policy', body),
    onSuccess: data => { qc.setQueryData(['admin-2fa-policy'], data); toast.success('Administrator 2FA policy saved') },
    onError: error => toast.error('Could not save 2FA policy', error.message),
  })
  if (query.isLoading) return <CardSkeleton className="mt-6" />
  if (query.error) return <Card className="mt-6"><CardContent><ErrorState error={query.error} onRetry={query.refetch} /></CardContent></Card>
  return <Card className="mt-6">
    <CardHeader><div><CardTitle>Administrator 2FA policy</CardTitle><CardDescription>Require active administrators to enroll in two-factor authentication before the grace period ends.</CardDescription></div></CardHeader>
    <CardContent className="space-y-5">
      <div className="flex items-center gap-3"><Switch checked={!!form?.require_admin_2fa} onCheckedChange={value => setForm(current => ({ ...current, require_admin_2fa: value }))} aria-label="Require 2FA for administrators" /><span className="text-sm text-foreground">Require 2FA for administrators</span></div>
      <FormField label="Grace period (days)" hint={`${query.data.administrators_with_2fa} active administrator${query.data.administrators_with_2fa === 1 ? '' : 's'} currently have 2FA. At least one is required before this policy can be enabled.`}>
        <Input type="number" min="0" max="30" className="max-w-[12rem]" value={form?.admin_2fa_grace_days ?? ''} onChange={event => setForm(current => ({ ...current, admin_2fa_grace_days: event.target.value }))} />
      </FormField>
    </CardContent>
    <CardFooter><Button disabled={!form || save.isPending} loading={save.isPending} onClick={() => save.mutate({ require_admin_2fa: !!form.require_admin_2fa, admin_2fa_grace_days: Number(form.admin_2fa_grace_days) })}><Save className="h-4 w-4" /> Save policy</Button></CardFooter>
  </Card>
}

export default function Security() {
  const username = useAccountUsername()
  const impersonating = useAuth((state) => state.impersonating)
  const role = useAuth((state) => state.role)
  const identitySynced = useAuth((state) => state.identitySynced)
  const qc = useQueryClient()

  // Enrollment is stateful across three server round-trips: setup returns a
  // pending secret + otpauth URI, verify confirms a live code (enabling 2FA and
  // handing back one-time recovery codes we must show exactly once).
  const [setupData, setSetupData] = useState(null)      // {secret, otpauth_uri, qr_data_uri}
  const [code, setCode] = useState('')
  const [codeError, setCodeError] = useState('')
  const [recoveryCodes, setRecoveryCodes] = useState(null)

  const [disableOpen, setDisableOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [passwordError, setPasswordError] = useState('')

  const { data: status, isLoading, error, refetch } = useQuery({
    queryKey: ['2fa-status', username],
    queryFn: () => get('/api/v1/2fa/status'),
    enabled: !!username && identitySynced && !impersonating,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['2fa-status', username] })

  const copyValue = async (text, label) => {
    const ok = await copyToClipboard(text)
    if (ok) toast.success(`${label} copied to clipboard`)
    else toast.error('Could not copy', 'Copy the value manually.')
  }

  const setupMut = useMutation({
    mutationFn: () => post('/api/v1/2fa/setup', { current_password: password }),
    onSuccess: (res) => { setSetupData(res); setCode(''); setCodeError('') },
    onError: (e) => toast.error('Could not start 2FA setup', e.message),
  })

  const verifyMut = useMutation({
    mutationFn: (c) => post('/api/v1/2fa/verify', { code: c, current_password: password }),
    onSuccess: (res) => {
      setRecoveryCodes(res.recovery_codes || [])
      setSetupData(null)
      setPassword('')
      setCode('')
      setCodeError('')
      toast.success('Two-factor authentication enabled', 'Save your recovery codes now — they are shown only once.')
      invalidate()
    },
    onError: (e) => { setCodeError(e.message); toast.error('Could not verify code', e.message) },
  })

  const disableMut = useMutation({
    mutationFn: (current_password) => post('/api/v1/2fa/disable', { current_password }),
    onSuccess: () => {
      toast.success('Two-factor authentication disabled', 'Your account no longer requires a second factor to sign in.')
      setDisableOpen(false)
      setPassword('')
      setPasswordError('')
      setRecoveryCodes(null)
      invalidate()
    },
    onError: (e) => { setPasswordError(e.message); toast.error('Could not disable 2FA', e.message) },
  })

  const submitVerify = (e) => {
    e.preventDefault()
    const c = code.trim()
    if (!c) { setCodeError('Enter the 6-digit code from your authenticator app.'); return }
    setCodeError('')
    verifyMut.mutate(c)
  }

  const submitDisable = (e) => {
    e.preventDefault()
    if (!password) { setPasswordError('Enter your current password to confirm.'); return }
    setPasswordError('')
    disableMut.mutate(password)
  }

  const downloadRecoveryCodes = () => {
    const body = `Boron two-factor recovery codes${username ? ` for ${username}` : ''}\nEach code works once. Keep them somewhere safe.\n\n${(recoveryCodes || []).join('\n')}\n`
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'boron-recovery-codes.txt'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  const enabled = !!status?.enabled

  if (impersonating) return <div>
    <PageHeader
      title="Two-factor authentication"
      description="Protect your account with a time-based one-time code from an authenticator app in addition to your password."
      icon={ShieldCheck}
    />
    <Card><CardContent className="py-6 text-sm text-muted-foreground">
      Two-factor authentication protects the customer’s panel login. Sign in directly as the customer to enroll, disable, or view its status.
    </CardContent></Card>
  </div>

  return (
    <div>
      <PageHeader
        title="Two-factor authentication"
        description="Protect your account with a time-based one-time code from an authenticator app in addition to your password."
        icon={ShieldCheck}
      >
        {enabled && !recoveryCodes && (
          <Button variant="danger" onClick={() => setDisableOpen(true)}>
            <ShieldOff className="h-4 w-4" /> Disable 2FA
          </Button>
        )}
      </PageHeader>

      {isLoading ? (
        <CardSkeleton />
      ) : error ? (
        <ErrorState error={error} onRetry={refetch} />
      ) : recoveryCodes ? (
        /* One-time recovery-code reveal — takes precedence over the enabled view. */
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-success" /> 2FA is enabled
            </CardTitle>
            <CardDescription>
              Save these {recoveryCodes.length} recovery codes now. Each one works a single time and can be used in place
              of a code if you lose access to your authenticator. They will never be shown again.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-start gap-2 rounded-btn border border-warning/20 bg-warning/10 px-3 py-2 text-sm text-[#B45309] dark:text-warning">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
              <span>Store these somewhere safe before continuing. You will not be able to see them again.</span>
            </div>
            <div className="grid grid-cols-2 gap-2 rounded-btn border border-border bg-muted p-3 sm:grid-cols-4">
              {recoveryCodes.map((c) => (
                <code key={c} className="select-all text-center font-mono text-sm tracking-wider text-foreground">{c}</code>
              ))}
            </div>
          </CardContent>
          <CardFooter className="flex flex-wrap justify-end gap-3">
            <Button variant="outline" onClick={() => copyValue(recoveryCodes.join('\n'), 'Recovery codes')}>
              <Copy className="h-4 w-4" /> Copy all
            </Button>
            <Button variant="outline" onClick={downloadRecoveryCodes}>
              <Download className="h-4 w-4" /> Download
            </Button>
            <Button onClick={() => setRecoveryCodes(null)}>I have saved my codes</Button>
          </CardFooter>
        </Card>
      ) : enabled ? (
        /* Enabled — offer disable. */
        <Card>
          <CardHeader>
            <CardTitle>Status</CardTitle>
            <CardDescription>Two-factor authentication is active on your account.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-btn bg-success/10 text-success">
                  <ShieldCheck className="h-5 w-5" />
                </div>
                <div>
                  <div className="mb-1"><StatusBadge status="enabled" /></div>
                  <div className="text-sm text-muted-foreground">
                    You will be asked for a code from your authenticator app when you sign in.
                  </div>
                </div>
              </div>
              <Button variant="danger" onClick={() => setDisableOpen(true)}>
                <ShieldOff className="h-4 w-4" /> Disable 2FA
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : setupData ? (
        /* Setup in progress — scan/enter the secret, then confirm a live code. */
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Smartphone className="h-5 w-5 text-accent-600 dark:text-accent-300" /> Scan the QR code
            </CardTitle>
            <CardDescription>
              Add Boron to an authenticator app (Google Authenticator, 1Password, Authy…), then enter the 6-digit code
              it shows to finish enabling 2FA.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="flex flex-col items-start gap-5 sm:flex-row">
              {setupData.qr_data_uri && (
                <img
                  src={setupData.qr_data_uri}
                  alt="2FA setup QR code"
                  width={176}
                  height={176}
                  className="h-44 w-44 shrink-0 rounded-btn border border-border bg-white p-3"
                />
              )}
              <div className="min-w-0 flex-1 space-y-4">
                <div>
                  <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Manual entry secret
                  </div>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 truncate rounded-btn border border-border bg-muted px-3 py-2 font-mono text-sm tracking-wider text-foreground">
                      {setupData.secret}
                    </code>
                    <Button variant="outline" size="icon" aria-label="Copy setup secret" onClick={() => copyValue(setupData.secret, 'Secret')}>
                      <Copy className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                <div>
                  <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    otpauth URI
                  </div>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 truncate rounded-btn border border-border bg-muted px-3 py-2 font-mono text-xs text-muted-foreground" title={setupData.otpauth_uri}>
                      {setupData.otpauth_uri}
                    </code>
                    <Button variant="outline" size="icon" aria-label="Copy otpauth URI" onClick={() => copyValue(setupData.otpauth_uri, 'otpauth URI')}>
                      <Copy className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              </div>
            </div>

            <form onSubmit={submitVerify} className="border-t border-border pt-5">
              <FormField
                label="Verification code"
                htmlFor="totp-code"
                required
                error={codeError}
                hint="Enter the current 6-digit code from your authenticator app."
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    id="totp-code"
                    autoFocus
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    placeholder="123456"
                    value={code}
                    invalid={!!codeError}
                    onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                    className="max-w-[10rem] font-mono tracking-[0.3em]"
                  />
                  <Button type="submit" loading={verifyMut.isPending} disabled={!code.trim()}>
                    <ShieldCheck className="h-4 w-4" /> Verify and enable
                  </Button>
                </div>
              </FormField>
            </form>
          </CardContent>
          <CardFooter>
            <Button
              variant="ghost"
              onClick={() => { setSetupData(null); setPassword(''); setCode(''); setCodeError('') }}
              disabled={verifyMut.isPending}
            >
              Cancel
            </Button>
          </CardFooter>
        </Card>
      ) : (
        /* Disabled — start enrollment. */
        <EmptyState
          icon={ShieldOff}
          title="Two-factor authentication is off"
          description="Add a second layer of protection. You will scan a QR code with an authenticator app and confirm a code to turn it on."
          action={(
            <div className="space-y-3"><label className="block">Current password<Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
            <Button disabled={!password} loading={setupMut.isPending} onClick={() => setupMut.mutate()}>
              <ShieldCheck className="h-4 w-4" /> Enable 2FA
            </Button></div>
          )}
        />
      )}

      <ActiveSessions />
      {role === 'admin' && <AdminTwoFactorPolicy />}

      {/* Disable — requires the current account password (checked server-side). */}
      <Dialog
        open={disableOpen}
        onOpenChange={(v) => { setDisableOpen(v); if (!v) { setPassword(''); setPasswordError('') } }}
      >
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Disable two-factor authentication?</DialogTitle>
            <DialogDescription>
              This removes 2FA and invalidates your recovery codes. Sign-in will only require your password. Enter your
              current password to confirm.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={submitDisable}>
            <DialogBody>
              <FormField label="Current password" htmlFor="disable-pw" required error={passwordError}>
                <Input
                  id="disable-pw"
                  type="password"
                  autoFocus
                  autoComplete="current-password"
                  value={password}
                  invalid={!!passwordError}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Your account password"
                  required
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setDisableOpen(false)} disabled={disableMut.isPending}>
                Cancel
              </Button>
              <Button type="submit" variant="danger" loading={disableMut.isPending} disabled={!password}>
                <ShieldOff className="h-4 w-4" /> Disable 2FA
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
