import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Gauge, KeyRound, RefreshCw, Server, Settings2 } from 'lucide-react'
import { get, post, put } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { FormField, Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Switch } from '@/components/ui/Toggle'
import { ConfirmDialog, Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/Dialog'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { toast } from '@/components/ui/Toast'

const PROFILES = {
  balanced: { max_connections: 10000, max_ssl_connections: 10000, connection_timeout: 300, keep_alive_timeout: 5, max_keep_alive_requests: 10000 },
  conservative: { max_connections: 3000, max_ssl_connections: 3000, connection_timeout: 120, keep_alive_timeout: 5, max_keep_alive_requests: 1000 },
  high: { max_connections: 30000, max_ssl_connections: 30000, connection_timeout: 180, keep_alive_timeout: 8, max_keep_alive_requests: 20000 },
}

export default function OpenLiteSpeed() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['ols-admin'], queryFn: () => get('/api/v1/admin/openlitespeed') })
  const [form, setForm] = useState(null)
  const [profile, setProfile] = useState('balanced')
  const [resetOpen, setResetOpen] = useState(false)
  const [credential, setCredential] = useState(null)
  useEffect(() => { if (query.data?.settings && form == null) setForm(query.data.settings) }, [query.data, form])
  const webadmin = useMemo(() => {
    if (typeof window === 'undefined') return ''
    return `https://${window.location.hostname}:${query.data?.webadmin_port || 7080}/`
  }, [query.data?.webadmin_port])
  const invalidate = () => qc.invalidateQueries({ queryKey: ['ols-admin'] })
  const saveMut = useMutation({ mutationFn: () => put('/api/v1/admin/openlitespeed/settings', form), onSuccess: (data) => { setForm(data.settings); toast.success('OpenLiteSpeed settings applied'); invalidate() }, onError: (error) => toast.error('Could not apply settings', error.message) })
  const reloadMut = useMutation({ mutationFn: () => post('/api/v1/admin/openlitespeed/reload', { confirm: true }), onSuccess: () => { toast.success('OpenLiteSpeed reloaded'); invalidate() }, onError: (error) => toast.error('Reload failed', error.message) })
  const revealMut = useMutation({ mutationFn: () => post('/api/v1/admin/openlitespeed/credentials/reveal', { confirm: true }), onSuccess: setCredential, onError: (error) => toast.error('Password is unavailable', error.message) })
  const resetMut = useMutation({ mutationFn: () => post('/api/v1/admin/openlitespeed/credentials/reset', { username: query.data?.credential?.username || 'admin' }), onSuccess: (data) => { setCredential(data); setResetOpen(false); toast.success('WebAdmin password reset'); invalidate() }, onError: (error) => toast.error('Password reset failed', error.message) })
  const number = (key) => (event) => setForm((value) => ({ ...value, [key]: Number(event.target.value) }))
  if (query.isLoading || !form) return <CenteredSpinner />
  const data = query.data
  return <div className="space-y-6">
    <PageHeader title="OpenLiteSpeed" description="Configure the web server, validate changes, and manage its WebAdmin login." icon={Server}>
      <Button asChild variant="outline"><a href={webadmin} target="_blank" rel="noreferrer"><ExternalLink className="h-4 w-4" /> Open WebAdmin</a></Button>
      <Button variant="secondary" loading={reloadMut.isPending} onClick={() => reloadMut.mutate()}><RefreshCw className="h-4 w-4" /> Graceful reload</Button>
    </PageHeader>

    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
      <Card><CardContent className="pt-5"><div className="text-sm text-muted-foreground">Service</div><Badge className="mt-2" variant={data.active ? 'success' : 'danger'}>{data.active ? 'Running' : 'Stopped'}</Badge></CardContent></Card>
      <Card><CardContent className="pt-5"><div className="text-sm text-muted-foreground">Configuration</div><Badge className="mt-2" variant={data.config_valid ? 'success' : 'danger'}>{data.config_valid ? 'Valid' : 'Invalid'}</Badge></CardContent></Card>
      <Card><CardContent className="pt-5"><div className="text-sm text-muted-foreground">Hosted accounts</div><div className="mt-1 text-2xl font-semibold">{data.accounts}</div></CardContent></Card>
      <Card><CardContent className="pt-5"><div className="text-sm text-muted-foreground">Virtual hosts</div><div className="mt-1 text-2xl font-semibold">{data.domains}</div></CardContent></Card>
      <Card><CardContent className="pt-5"><div className="text-sm text-muted-foreground">WebAdmin HTTPS</div><Badge className="mt-2" variant={data.webadmin_tls_valid ? 'success' : 'warning'}>{data.webadmin_tls_valid ? 'Trusted certificate' : 'Certificate needs attention'}</Badge><div className="mt-2 truncate text-xs text-muted-foreground" title={data.webadmin_tls_hostname}>{data.webadmin_tls_hostname}:7080</div></CardContent></Card>
    </div>

    <Card><CardHeader><CardTitle><Gauge className="h-5 w-5" /> Server settings</CardTitle><CardDescription>Changes are stored in Boron, rendered into the managed OLS configuration, validated, reloaded, and rolled back if verification fails.</CardDescription></CardHeader><CardContent className="space-y-5">
      <FormField label="Performance profile" hint="Choose a starting point, then adjust individual values."><Select value={profile} onChange={(event) => { const value = event.target.value; setProfile(value); setForm((current) => ({ ...current, ...PROFILES[value] })) }}><option value="balanced">Balanced (recommended)</option><option value="conservative">Conservative / small server</option><option value="high">High traffic</option></Select></FormField>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <FormField label="Maximum connections"><Input aria-label="Maximum connections" type="number" min="100" max="1000000" value={form.max_connections} onChange={number('max_connections')} /></FormField>
        <FormField label="Maximum SSL connections"><Input type="number" min="100" max="1000000" value={form.max_ssl_connections} onChange={number('max_ssl_connections')} /></FormField>
        <FormField label="Connection timeout (seconds)"><Input type="number" min="10" max="3600" value={form.connection_timeout} onChange={number('connection_timeout')} /></FormField>
        <FormField label="Keep-alive timeout (seconds)"><Input type="number" min="1" max="120" value={form.keep_alive_timeout} onChange={number('keep_alive_timeout')} /></FormField>
        <FormField label="Keep-alive requests"><Input type="number" min="100" max="100000" value={form.max_keep_alive_requests} onChange={number('max_keep_alive_requests')} /></FormField>
        <FormField label="Log retention"><Select value={form.log_keep_days} onChange={number('log_keep_days')}><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option><option value="60">60 days</option><option value="90">90 days</option></Select></FormField>
        <FormField label="Log level"><Select value={form.log_level} onChange={(event) => setForm((value) => ({ ...value, log_level: event.target.value }))}><option>ERROR</option><option>WARN</option><option>NOTICE</option><option>INFO</option><option>DEBUG</option></Select></FormField>
        <FormField label="Gzip compression level"><Select value={form.gzip_level} onChange={number('gzip_level')}>{[1, 3, 4, 5, 6, 7, 8, 9].map((value) => <option key={value} value={value}>{value}{value === 6 ? ' (recommended)' : ''}</option>)}</Select></FormField>
        <FormField label="Brotli compression level"><Select value={form.brotli_level} onChange={number('brotli_level')}>{[1, 3, 4, 5, 6, 7, 8, 9, 10, 11].map((value) => <option key={value} value={value}>{value}{value === 6 ? ' (recommended)' : ''}</option>)}</Select></FormField>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">{[['gzip_enabled', 'Dynamic Gzip'], ['brotli_enabled', 'Brotli'], ['quic_enabled', 'HTTP/3 / QUIC']].map(([key, label]) => <label key={key} className="flex items-center justify-between rounded-btn border border-border p-3 text-sm"><span>{label}</span><Switch aria-label={label} checked={form[key]} onCheckedChange={(checked) => setForm((value) => ({ ...value, [key]: checked }))} /></label>)}</div>
      {data.config_message && <p className="break-all rounded-btn bg-muted p-3 font-mono text-xs text-muted-foreground">{data.config_message}</p>}
      <Button loading={saveMut.isPending} onClick={() => saveMut.mutate()}><Settings2 className="h-4 w-4" /> Validate and apply</Button>
    </CardContent></Card>

    <Card><CardHeader><CardTitle><KeyRound className="h-5 w-5" /> WebAdmin credentials</CardTitle><CardDescription>The installed one-way password hash cannot be decoded. After a reset, Boron can reveal the newly generated credential to authenticated administrators.</CardDescription></CardHeader><CardContent className="flex flex-wrap items-center justify-between gap-4">
      <div><div className="text-sm font-medium">Username: {data.credential?.username || 'admin'}</div><div className="text-sm text-muted-foreground">{data.credential?.password_available ? 'A Boron-managed password is available.' : 'Reset once to make a password available.'}</div></div>
      <div className="flex gap-2">{data.credential?.password_available && <Button variant="outline" loading={revealMut.isPending} onClick={() => revealMut.mutate()}>View password</Button>}<Button variant="danger" onClick={() => setResetOpen(true)}>Reset password</Button></div>
    </CardContent></Card>

    <ConfirmDialog open={resetOpen} onOpenChange={setResetOpen} title="Reset WebAdmin password?" description="A strong password will replace the current OpenLiteSpeed WebAdmin credential and be shown once the reset succeeds." confirmLabel="Reset password" variant="danger" loading={resetMut.isPending} onConfirm={() => resetMut.mutate()} />
    <Dialog open={!!credential} onOpenChange={(open) => !open && setCredential(null)}><DialogContent size="sm"><DialogHeader><DialogTitle>WebAdmin login</DialogTitle><DialogDescription>Store this credential securely.</DialogDescription></DialogHeader><DialogBody className="space-y-4"><FormField label="Username"><Input readOnly value={credential?.username || ''} /></FormField><FormField label="Password"><Input readOnly value={credential?.password || ''} /></FormField></DialogBody><DialogFooter><Button variant="outline" onClick={() => navigator.clipboard.writeText(credential?.password || '').then(() => toast.success('Password copied'))}>Copy password</Button><Button onClick={() => setCredential(null)}>Done</Button></DialogFooter></DialogContent></Dialog>
  </div>
}
