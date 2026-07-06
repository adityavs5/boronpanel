import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Globe, Plus, Pencil, Trash2, Save, RefreshCw,
  ShieldOff, Ban, Lock, Users, Network, ShieldCheck,
  Server, Copy, ExternalLink, CheckCircle2, AlertCircle, Download, RotateCcw,
} from 'lucide-react'
import { get, post, put, patch, del } from '@/lib/api'
import { relativeTime, copyToClipboard } from '@/lib/utils'
import { PHP_VERSIONS } from '@/config/constants'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { DataTable } from '@/components/ui/Table'
import { Input, Textarea, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Switch } from '@/components/ui/Toggle'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { ErrorState } from '@/components/ui/States'
import { ProgressBar } from '@/components/ui/Progress'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { useAccountUsername } from '@/hooks/useAccount'

const DNS_TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SRV', 'CAA']
const linesToList = (text) => (text || '').split('\n').map((s) => s.trim()).filter(Boolean)

// ---------------------------------------------------------------------------
// DNS
// ---------------------------------------------------------------------------
function DnsTab({ domain }) {
  const qc = useQueryClient()
  const [dialog, setDialog] = useState(null) // {mode, subdomain, type, ttl, values}
  const [toDelete, setToDelete] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['dns-records', domain],
    queryFn: () => get(`/api/v1/dns/zones/${domain}/records`),
    enabled: !!domain,
  })

  const zone = (data?.zone || domain || '').replace(/\.$/, '')
  const rows = (data?.records || []).map((r) => {
    const name = (r.name || '').replace(/\.$/, '')
    let subdomain = '@'
    if (name === zone || !name) subdomain = '@'
    else if (zone && name.endsWith(`.${zone}`)) subdomain = name.slice(0, -(zone.length + 1))
    else subdomain = name
    const values = Array.isArray(r.values)
      ? r.values
      : Array.isArray(r.records)
        ? r.records.map((x) => (typeof x === 'string' ? x : x.content))
        : []
    return { subdomain, type: r.type, ttl: r.ttl, values, _key: `${subdomain}|${r.type}` }
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['dns-records', domain] })

  const saveMut = useMutation({
    mutationFn: (form) =>
      put(`/api/v1/dns/zones/${domain}/records`, {
        domain,
        subdomain: form.subdomain?.trim() || '@',
        type: form.type,
        values: linesToList(form.values),
        ttl: Number(form.ttl) || 3600,
      }),
    onSuccess: () => { toast.success('DNS record saved'); invalidate(); setDialog(null) },
    onError: (e) => toast.error('Could not save record', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (row) => del(`/api/v1/dns/zones/${domain}/records`, { params: { subdomain: row.subdomain, type: row.type } }),
    onSuccess: () => { toast.success('DNS record deleted'); invalidate(); setToDelete(null) },
    onError: (e) => { toast.error('Could not delete record', e.message); setToDelete(null) },
  })

  const columns = [
    { key: 'subdomain', header: 'Name', sortable: true, searchable: true, render: (r) => <span className="font-mono text-xs">{r.subdomain}</span> },
    { key: 'type', header: 'Type', sortable: true, render: (r) => <span className="font-mono text-xs">{r.type}</span> },
    { key: 'ttl', header: 'TTL', sortable: true, render: (r) => r.ttl },
    { key: 'values', header: 'Value(s)', render: (r) => <div className="whitespace-pre-line break-all font-mono text-xs">{r.values.join('\n')}</div> },
    {
      key: 'actions', header: '', align: 'right', render: (r) => (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="icon-sm" title="Edit"
            onClick={() => setDialog({ mode: 'edit', subdomain: r.subdomain, type: r.type, ttl: String(r.ttl), values: r.values.join('\n') })}>
            <Pencil className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon-sm" title="Delete" onClick={() => setToDelete(r)}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ),
    },
  ]

  const isEdit = dialog?.mode === 'edit'

  return (
    <div>
      <div className="mb-3 flex justify-end">
        <Button size="sm" onClick={() => setDialog({ mode: 'add', subdomain: '@', type: 'A', ttl: '3600', values: '' })}>
          <Plus className="h-4 w-4" /> Add record
        </Button>
      </div>

      <DataTable
        columns={columns}
        data={rows}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        getRowKey={(r) => r._key}
        filterable
        pageSize={15}
        searchPlaceholder="Search records…"
        emptyTitle="No DNS records"
        emptyDescription="Add a record to start managing this domain's zone."
        emptyIcon={Network}
      />

      <Dialog open={!!dialog} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>{isEdit ? 'Edit DNS record' : 'Add DNS record'}</DialogTitle>
            <DialogDescription>Saving a name + type replaces its entire value list (one value per line).</DialogDescription>
          </DialogHeader>
          {dialog && (
            <form onSubmit={(e) => { e.preventDefault(); saveMut.mutate(dialog) }}>
              <DialogBody className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <FormField label="Name" hint="@ for the apex, or e.g. www">
                    <Input value={dialog.subdomain} disabled={isEdit}
                      onChange={(e) => setDialog((d) => ({ ...d, subdomain: e.target.value }))} placeholder="@" />
                  </FormField>
                  <FormField label="Type">
                    <Select value={dialog.type} disabled={isEdit}
                      onChange={(e) => setDialog((d) => ({ ...d, type: e.target.value }))}>
                      {DNS_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                    </Select>
                  </FormField>
                </div>
                <FormField label="TTL (seconds)">
                  <Input type="number" min="60" value={dialog.ttl}
                    onChange={(e) => setDialog((d) => ({ ...d, ttl: e.target.value }))} />
                </FormField>
                <FormField label="Value(s)" required hint="One value per line. MX: '10 mail.example.com' · TXT: 'v=spf1 -all'">
                  <Textarea required rows={3} value={dialog.values}
                    onChange={(e) => setDialog((d) => ({ ...d, values: e.target.value }))} placeholder="1.2.3.4" />
                </FormField>
              </DialogBody>
              <DialogFooter>
                <Button type="button" variant="secondary" onClick={() => setDialog(null)}>Cancel</Button>
                <Button type="submit" loading={saveMut.isPending}>Save record</Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={toDelete ? `Delete ${toDelete.type} record at ${toDelete.subdomain}?` : ''}
        description="This removes the entire rrset (all values shown)."
        confirmLabel="Delete record"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(toDelete)}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Nameservers
// ---------------------------------------------------------------------------
function NameserversForm({ username, domain, nameservers, glue }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/domains/${domain}/nameservers`
  const [nsText, setNsText] = useState(nameservers.join('\n'))
  const [glueText, setGlueText] = useState(
    Object.entries(glue)
      .map(([host, v]) => {
        const ip = (v?.a && v.a[0]) || (v?.aaaa && v.aaaa[0]) || ''
        return `${host}=${ip}`
      })
      .join('\n'),
  )
  const [resetOpen, setResetOpen] = useState(false)
  const invalidate = () => qc.invalidateQueries({ queryKey: ['nameservers', username, domain] })

  const saveMut = useMutation({
    mutationFn: () => {
      const glueMap = {}
      for (const line of (glueText || '').split('\n')) {
        const trimmed = line.trim()
        const eq = trimmed.indexOf('=')
        if (eq < 1) continue
        const host = trimmed.slice(0, eq).trim()
        const ip = trimmed.slice(eq + 1).trim()
        if (host && ip) glueMap[host] = ip
      }
      return put(base, { nameservers: linesToList(nsText), glue: glueMap })
    },
    onSuccess: () => { toast.success('Nameservers saved'); invalidate() },
    onError: (e) => toast.error('Could not save nameservers', e.message),
  })

  const resetMut = useMutation({
    mutationFn: () => del(base),
    onSuccess: () => { toast.success('Nameservers reset to defaults'); invalidate(); setResetOpen(false) },
    onError: (e) => { toast.error('Could not reset nameservers', e.message); setResetOpen(false) },
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Set custom nameservers</CardTitle>
        <CardDescription>
          One hostname per line. If a nameserver is itself a subdomain of {domain}, provide its own IP as a
          glue record below (it can't be resolved before the delegation to it works).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <FormField label="Nameserver hostnames" required hint="One per line — e.g. ns1.example.com">
          <Textarea rows={4} value={nsText} placeholder={`ns1.${domain}\nns2.${domain}`}
            onChange={(e) => setNsText(e.target.value)} />
        </FormField>
        <FormField label="Glue records"
          hint={`Only for nameservers that are subdomains of ${domain}. One per line, as hostname=ip.`}>
          <Textarea rows={3} value={glueText} placeholder={`ns1.${domain}=203.0.113.10`}
            onChange={(e) => setGlueText(e.target.value)} />
        </FormField>
      </CardContent>
      <CardFooter className="flex gap-2">
        <Button loading={saveMut.isPending} disabled={!linesToList(nsText).length} onClick={() => saveMut.mutate()}>
          <Save className="h-4 w-4" /> Save nameservers
        </Button>
        <Button variant="outline" onClick={() => setResetOpen(true)}>
          <RotateCcw className="h-4 w-4" /> Reset to defaults
        </Button>
      </CardFooter>

      <ConfirmDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        title="Reset to default nameservers?"
        description={`This restores Forgehost-managed defaults (ns1.${domain} / ns2.${domain}) pointing at this server.`}
        confirmLabel="Reset to defaults"
        variant="danger"
        loading={resetMut.isPending}
        onConfirm={() => resetMut.mutate()}
      />
    </Card>
  )
}

function NameserversTab({ username, domain }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['nameservers', username, domain],
    queryFn: () => get(`/api/v1/accounts/${username}/domains/${domain}/nameservers`),
    enabled: !!username && !!domain,
  })

  const nameservers = data?.nameservers || []
  const glue = data?.glue || {}
  const rows = nameservers.map((ns) => {
    const g = glue[ns]
    const ips = g ? [...(g.a || []), ...(g.aaaa || [])] : []
    return { hostname: ns, ips, needsGlue: !!g }
  })

  const columns = [
    { key: 'hostname', header: 'Hostname', searchable: true, render: (r) => <span className="font-mono text-xs">{r.hostname}</span> },
    {
      key: 'glue', header: 'Glue (A / AAAA)', render: (r) =>
        r.needsGlue
          ? <span className="font-mono text-xs">{r.ips.length ? r.ips.join(', ') : '—'}</span>
          : <span className="text-xs text-muted-foreground">external — no glue needed</span>,
    },
  ]

  return (
    <div className="space-y-6">
      <DataTable
        columns={columns}
        data={rows}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        getRowKey={(r) => r.hostname}
        pageSize={0}
        emptyTitle="No nameservers set"
        emptyDescription="This zone has no NS records at its apex yet."
        emptyIcon={Server}
      />
      {data && (
        <NameserversForm key={nameservers.join(',')} username={username} domain={domain} nameservers={nameservers} glue={glue} />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Redirects
// ---------------------------------------------------------------------------
function RedirectsTab({ username, domain }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/domains/${domain}/redirects`
  const [dialog, setDialog] = useState(null) // {mode, path, target_url, status_code}
  const [toDelete, setToDelete] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['redirects', username, domain],
    queryFn: () => get(base),
    enabled: !!username && !!domain,
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['redirects', username, domain] })

  const saveMut = useMutation({
    mutationFn: (form) => {
      const body = { path: form.path.trim(), target_url: form.target_url.trim(), status_code: Number(form.status_code) }
      return form.mode === 'edit' ? put(base, body) : post(base, body)
    },
    onSuccess: () => { toast.success('Redirect saved'); invalidate(); setDialog(null) },
    onError: (e) => toast.error('Could not save redirect', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (row) => del(base, { params: { path: row.path } }),
    onSuccess: () => { toast.success('Redirect deleted'); invalidate(); setToDelete(null) },
    onError: (e) => { toast.error('Could not delete redirect', e.message); setToDelete(null) },
  })

  const columns = [
    { key: 'path', header: 'Path', sortable: true, searchable: true, render: (r) => <span className="font-mono text-xs">{r.path}</span> },
    { key: 'target_url', header: 'Target URL', searchable: true, render: (r) => <span className="break-all font-mono text-xs">{r.target_url}</span> },
    { key: 'status_code', header: 'Code', sortable: true },
    {
      key: 'actions', header: '', align: 'right', render: (r) => (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="icon-sm" title="Edit"
            onClick={() => setDialog({ mode: 'edit', path: r.path, target_url: r.target_url, status_code: String(r.status_code) })}>
            <Pencil className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon-sm" title="Delete" onClick={() => setToDelete(r)}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ),
    },
  ]

  const isEdit = dialog?.mode === 'edit'

  return (
    <div>
      <div className="mb-3 flex justify-end">
        <Button size="sm" onClick={() => setDialog({ mode: 'add', path: '', target_url: '', status_code: '301' })}>
          <Plus className="h-4 w-4" /> Add redirect
        </Button>
      </div>

      <DataTable
        columns={columns}
        data={data?.redirects}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        getRowKey={(r) => r.id ?? r.path}
        pageSize={15}
        emptyTitle="No redirects yet"
        emptyDescription="Send visitors from an old path to a new URL."
        emptyIcon={RefreshCw}
      />

      <Dialog open={!!dialog} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>{isEdit ? 'Edit redirect' : 'Add redirect'}</DialogTitle>
          </DialogHeader>
          {dialog && (
            <form onSubmit={(e) => { e.preventDefault(); saveMut.mutate(dialog) }}>
              <DialogBody className="space-y-4">
                <FormField label="Path" required hint="Path on this domain, e.g. /old-page">
                  <Input required value={dialog.path} disabled={isEdit} placeholder="/old-page"
                    onChange={(e) => setDialog((d) => ({ ...d, path: e.target.value }))} />
                </FormField>
                <FormField label="Target URL" required>
                  <Input required type="url" value={dialog.target_url} placeholder="https://example.com/new-page"
                    onChange={(e) => setDialog((d) => ({ ...d, target_url: e.target.value }))} />
                </FormField>
                <FormField label="Status code">
                  <Select value={dialog.status_code} onChange={(e) => setDialog((d) => ({ ...d, status_code: e.target.value }))}>
                    <option value="301">301 (permanent)</option>
                    <option value="302">302 (temporary)</option>
                  </Select>
                </FormField>
              </DialogBody>
              <DialogFooter>
                <Button type="button" variant="secondary" onClick={() => setDialog(null)}>Cancel</Button>
                <Button type="submit" loading={saveMut.isPending}>Save redirect</Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={toDelete ? `Delete redirect for ${toDelete.path}?` : ''}
        confirmLabel="Delete redirect"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(toDelete)}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Cache (LSCache)
// ---------------------------------------------------------------------------
function LscacheForm({ username, domain, settings }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/domains/${domain}/lscache`
  const [form, setForm] = useState({
    enabled: !!settings.enabled,
    ttl_seconds: settings.ttl_seconds ?? 3600,
    exclude_paths: (settings.exclude_paths || []).join('\n'),
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['lscache', username, domain] })

  const saveMut = useMutation({
    mutationFn: () => put(base, {
      enabled: form.enabled,
      ttl_seconds: Number(form.ttl_seconds) || 3600,
      exclude_paths: linesToList(form.exclude_paths),
    }),
    onSuccess: () => { toast.success('Cache settings saved'); invalidate() },
    onError: (e) => toast.error('Could not save cache settings', e.message),
  })

  const purgeMut = useMutation({
    mutationFn: () => post(`${base}/purge`),
    onSuccess: () => { toast.success('Cache purged'); invalidate() },
    onError: (e) => toast.error('Could not purge cache', e.message),
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>LiteSpeed cache</CardTitle>
        <CardDescription>
          {settings.last_purged_at ? `Last purged ${relativeTime(settings.last_purged_at)}` : 'Cache has never been purged.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-foreground">Enable full-page cache</p>
            <p className="text-xs text-muted-foreground">Serve cached responses for this domain.</p>
          </div>
          <Switch checked={form.enabled} onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: v }))} />
        </div>
        <FormField label="Cache TTL (seconds)">
          <Input type="number" min="0" value={form.ttl_seconds}
            onChange={(e) => setForm((f) => ({ ...f, ttl_seconds: e.target.value }))} />
        </FormField>
        <FormField label="Excluded paths" hint="One path per line — these are never cached.">
          <Textarea rows={4} value={form.exclude_paths} placeholder="/cart&#10;/checkout"
            onChange={(e) => setForm((f) => ({ ...f, exclude_paths: e.target.value }))} />
        </FormField>
      </CardContent>
      <CardFooter className="flex gap-2">
        <Button loading={saveMut.isPending} onClick={() => saveMut.mutate()}>
          <Save className="h-4 w-4" /> Save settings
        </Button>
        <Button variant="outline" loading={purgeMut.isPending} onClick={() => purgeMut.mutate()}>
          <RefreshCw className="h-4 w-4" /> Purge cache
        </Button>
      </CardFooter>
    </Card>
  )
}

function CacheTab({ username, domain }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['lscache', username, domain],
    queryFn: () => get(`/api/v1/accounts/${username}/domains/${domain}/lscache`),
    enabled: !!username && !!domain,
  })
  if (isLoading) return <CardSkeleton />
  if (error) return <ErrorState error={error} onRetry={refetch} />
  if (!data) return null
  // Remount when server state changes so the form re-seeds from fresh settings.
  return <LscacheForm key={data.last_purged_at || 'lscache'} username={username} domain={domain} settings={data} />
}

// ---------------------------------------------------------------------------
// PHP
// ---------------------------------------------------------------------------
function PhpTab({ username, domain }) {
  const qc = useQueryClient()
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })

  const current = (data?.domains || []).find((d) => d.domain === domain)
  const [phpVersion, setPhpVersion] = useState('')
  // Seed the select once the domain row loads.
  const [seeded, setSeeded] = useState(false)
  if (!seeded && current) { setPhpVersion(current.php_version || ''); setSeeded(true) }

  const phpMut = useMutation({
    mutationFn: () => patch(`/api/v1/accounts/${username}/domains/${domain}/php-version`, { php_version: phpVersion || null }),
    onSuccess: () => { toast.success('PHP version updated'); qc.invalidateQueries({ queryKey: ['domains', username] }) },
    onError: (e) => toast.error('Could not update PHP version', e.message),
  })

  const sslMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${username}/domains/${domain}/ssl/issue`, { force: false }),
    onSuccess: () => toast.success('SSL issuance started', `A certificate for ${domain} is being requested.`),
    onError: (e) => toast.error('Could not issue SSL', e.message),
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>PHP version</CardTitle>
          <CardDescription>Override the PHP version for this domain, or inherit the account default.</CardDescription>
        </CardHeader>
        <CardContent>
          <FormField label="Version">
            <Select value={phpVersion} onChange={(e) => setPhpVersion(e.target.value)}>
              <option value="">Use account default</option>
              {PHP_VERSIONS.map((v) => <option key={v} value={v}>PHP {v}</option>)}
            </Select>
          </FormField>
        </CardContent>
        <CardFooter>
          <Button loading={phpMut.isPending} onClick={() => phpMut.mutate()}>
            <Save className="h-4 w-4" /> Save PHP version
          </Button>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>SSL certificate</CardTitle>
          <CardDescription>Issue or renew a Let's Encrypt certificate for this domain.</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Requesting a certificate requires this domain's DNS to resolve to this server.
          </p>
        </CardContent>
        <CardFooter>
          <Button variant="outline" loading={sslMut.isPending} onClick={() => sslMut.mutate()}>
            <ShieldCheck className="h-4 w-4" /> Issue SSL
          </Button>
        </CardFooter>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Security — Hotlink protection
// ---------------------------------------------------------------------------
function HotlinkCard({ username, domain }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/domains/${domain}/hotlink-protection`
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['hotlink', username, domain],
    queryFn: () => get(base),
    enabled: !!username && !!domain,
  })

  const [seeded, setSeeded] = useState(false)
  const [form, setForm] = useState({ enabled: false, allowed_domains: '' })
  if (!seeded && data) {
    setForm({ enabled: !!data.enabled, allowed_domains: (data.allowed_domains || []).join('\n') })
    setSeeded(true)
  }

  const saveMut = useMutation({
    mutationFn: () => patch(base, { enabled: form.enabled, allowed_domains: linesToList(form.allowed_domains) }),
    onSuccess: () => { toast.success('Hotlink protection saved'); qc.invalidateQueries({ queryKey: ['hotlink', username, domain] }) },
    onError: (e) => toast.error('Could not save hotlink protection', e.message),
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><ShieldOff className="h-4 w-4" /> Hotlink protection</CardTitle>
        <CardDescription>Block other sites from embedding your images and media by Referer.</CardDescription>
      </CardHeader>
      {isLoading ? (
        <CardContent><CardSkeleton /></CardContent>
      ) : error ? (
        <CardContent><ErrorState error={error} onRetry={refetch} /></CardContent>
      ) : (
        <>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium text-foreground">Enabled</p>
              <Switch checked={form.enabled} onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: v }))} />
            </div>
            <FormField label="Allowed external domains" hint="One per line — e.g. a CDN or a second site of yours.">
              <Textarea rows={3} value={form.allowed_domains} placeholder="cdn.example.com"
                onChange={(e) => setForm((f) => ({ ...f, allowed_domains: e.target.value }))} />
            </FormField>
          </CardContent>
          <CardFooter>
            <Button loading={saveMut.isPending} onClick={() => saveMut.mutate()}>
              <Save className="h-4 w-4" /> Save
            </Button>
          </CardFooter>
        </>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Security — IP blocker
// ---------------------------------------------------------------------------
function IpBlockCard({ username, domain }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/domains/${domain}/ip-block`
  const [entry, setEntry] = useState('')
  const [toDelete, setToDelete] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['ip-block', username, domain],
    queryFn: () => get(base),
    enabled: !!username && !!domain,
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['ip-block', username, domain] })

  const addMut = useMutation({
    mutationFn: () => post(base, { entry: entry.trim() }),
    onSuccess: () => { toast.success('IP blocked'); setEntry(''); invalidate() },
    onError: (e) => toast.error('Could not block IP', e.message),
  })
  const deleteMut = useMutation({
    mutationFn: (row) => del(base, { params: { entry: row.entry } }),
    onSuccess: () => { toast.success('IP unblocked'); invalidate(); setToDelete(null) },
    onError: (e) => { toast.error('Could not unblock IP', e.message); setToDelete(null) },
  })

  const rows = (data?.blocked || []).map((e) => ({ entry: e }))
  const columns = [
    { key: 'entry', header: 'IP / CIDR', searchable: true, render: (r) => <span className="font-mono text-xs">{r.entry}</span> },
    {
      key: 'actions', header: '', align: 'right', render: (r) => (
        <Button variant="ghost" size="icon-sm" title="Unblock" onClick={() => setToDelete(r)}>
          <Trash2 className="h-4 w-4" />
        </Button>
      ),
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Ban className="h-4 w-4" /> IP blocker</CardTitle>
        <CardDescription>Requests from a blocked IP or CIDR range get a 403.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (entry.trim()) addMut.mutate() }}>
          <Input value={entry} onChange={(e) => setEntry(e.target.value)} placeholder="203.0.113.7 or 203.0.113.0/24" />
          <Button type="submit" loading={addMut.isPending} disabled={!entry.trim()}><Plus className="h-4 w-4" /> Block</Button>
        </form>
        <DataTable
          columns={columns}
          data={rows}
          loading={isLoading}
          error={error}
          onRetry={refetch}
          getRowKey={(r) => r.entry}
          pageSize={10}
          emptyTitle="No IPs blocked"
          emptyDescription="Add an IP or CIDR range above."
          emptyIcon={Ban}
        />
      </CardContent>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={toDelete ? `Unblock ${toDelete.entry}?` : ''}
        confirmLabel="Unblock"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(toDelete)}
      />
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Security — Directory privacy (file-auth, account-scoped)
// ---------------------------------------------------------------------------
function ManageUsersDialog({ username, dir, open, onOpenChange }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/file-auth/users`
  const [form, setForm] = useState({ htuser: '', password: '' })

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['file-auth-users', username, dir],
    queryFn: () => get(base, { params: { path: dir } }),
    enabled: open && !!dir,
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['file-auth-users', username, dir] })

  const addMut = useMutation({
    mutationFn: () => post(base, { path: dir, htuser: form.htuser.trim(), password: form.password }),
    onSuccess: () => { toast.success('User saved'); setForm({ htuser: '', password: '' }); invalidate() },
    onError: (e) => toast.error('Could not save user', e.message),
  })
  const deleteMut = useMutation({
    mutationFn: (htuser) => del(base, { params: { path: dir, htuser } }),
    onSuccess: () => { toast.success('User deleted'); invalidate() },
    onError: (e) => toast.error('Could not delete user', e.message),
  })

  const users = data?.users || []

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Users for {dir}</DialogTitle>
          <DialogDescription>Visitors must sign in with one of these users to view this directory.</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-4">
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : error ? (
            <ErrorState error={error} onRetry={refetch} />
          ) : users.length === 0 ? (
            <p className="text-sm text-muted-foreground">No users yet — this directory is locked out until you add one.</p>
          ) : (
            <ul className="divide-y divide-border rounded-btn border border-border">
              {users.map((u) => (
                <li key={u} className="flex items-center justify-between px-3 py-2 text-sm">
                  <span className="font-mono text-xs">{u}</span>
                  <Button variant="ghost" size="icon-sm" title="Delete user" loading={deleteMut.isPending}
                    onClick={() => deleteMut.mutate(u)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <form className="space-y-3 border-t border-border pt-4"
            onSubmit={(e) => { e.preventDefault(); if (form.htuser.trim() && form.password) addMut.mutate() }}>
            <div className="grid grid-cols-2 gap-3">
              <FormField label="Username">
                <Input value={form.htuser} placeholder="member"
                  onChange={(e) => setForm((f) => ({ ...f, htuser: e.target.value }))} />
              </FormField>
              <FormField label="Password">
                <Input type="password" value={form.password}
                  onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} />
              </FormField>
            </div>
            <Button type="submit" size="sm" loading={addMut.isPending} disabled={!form.htuser.trim() || !form.password}>
              <Plus className="h-4 w-4" /> Add / update user
            </Button>
          </form>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DirectoryPrivacyCard({ username }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/file-auth`
  const [path, setPath] = useState('')
  const [toDelete, setToDelete] = useState(null)
  const [manageDir, setManageDir] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['file-auth', username],
    queryFn: () => get(base),
    enabled: !!username,
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['file-auth', username] })

  const addMut = useMutation({
    mutationFn: () => post(base, { path: path.trim() }),
    onSuccess: () => { toast.success('Directory protected'); setPath(''); invalidate() },
    onError: (e) => toast.error('Could not protect directory', e.message),
  })
  const deleteMut = useMutation({
    mutationFn: (row) => del(base, { params: { path: row.path } }),
    onSuccess: () => { toast.success('Protection removed'); invalidate(); setToDelete(null) },
    onError: (e) => { toast.error('Could not remove protection', e.message); setToDelete(null) },
  })

  const columns = [
    { key: 'path', header: 'Directory', searchable: true, render: (r) => <span className="font-mono text-xs">{r.path}</span> },
    {
      key: 'actions', header: '', align: 'right', render: (r) => (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="icon-sm" title="Manage users" onClick={() => setManageDir(r.path)}>
            <Users className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon-sm" title="Remove protection" onClick={() => setToDelete(r)}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ),
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Lock className="h-4 w-4" /> Directory privacy</CardTitle>
        <CardDescription>Password-protect a directory under your account (path relative to your home).</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (path.trim()) addMut.mutate() }}>
          <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="public_html/members" />
          <Button type="submit" loading={addMut.isPending} disabled={!path.trim()}><Plus className="h-4 w-4" /> Protect</Button>
        </form>
        <DataTable
          columns={columns}
          data={data?.protected}
          loading={isLoading}
          error={error}
          onRetry={refetch}
          getRowKey={(r) => r.path}
          pageSize={10}
          emptyTitle="No protected directories"
          emptyDescription="Protect a directory above, then add users to it."
          emptyIcon={Lock}
        />
      </CardContent>

      {manageDir && (
        <ManageUsersDialog username={username} dir={manageDir} open={!!manageDir} onOpenChange={(o) => !o && setManageDir(null)} />
      )}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={toDelete ? `Remove protection from ${toDelete.path}?` : ''}
        description="Visitors will no longer be prompted for a password."
        confirmLabel="Remove protection"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(toDelete)}
      />
    </Card>
  )
}

function SecurityTab({ username, domain }) {
  return (
    <div className="space-y-6">
      <HotlinkCard username={username} domain={domain} />
      <IpBlockCard username={username} domain={domain} />
      <DirectoryPrivacyCard username={username} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// WordPress — one-click installer with job polling
// ---------------------------------------------------------------------------
const WP_PROGRESS = {
  queued: 10,
  'downloading WordPress core': 40,
  'creating database': 70,
  done: 100,
  failed: 100,
}

function wpPercent(job) {
  if (!job) return 0
  if (job.status === 'completed' || job.status === 'failed') return 100
  return WP_PROGRESS[job.progress_message] ?? (job.status === 'running' ? 60 : 10)
}

function CopyRow({ label, value, mono = true, href }) {
  const copy = () => { copyToClipboard(value); toast.success(`${label} copied`) }
  return (
    <div className="flex items-center justify-between gap-3 rounded-btn border border-border px-3 py-2">
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">{label}</p>
        {href ? (
          <a href={href} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1 break-all text-sm text-accent hover:underline">
            {value} <ExternalLink className="h-3.5 w-3.5 shrink-0" />
          </a>
        ) : (
          <p className={`break-all text-sm text-foreground ${mono ? 'font-mono' : ''}`}>{value}</p>
        )}
      </div>
      <Button variant="ghost" size="icon-sm" title={`Copy ${label.toLowerCase()}`} onClick={copy}>
        <Copy className="h-4 w-4" />
      </Button>
    </div>
  )
}

function WordPressTab({ username, domain }) {
  const base = `/api/v1/accounts/${username}/domains/${domain}/wordpress`
  const [form, setForm] = useState({ title: '', admin_user: 'admin', admin_email: '', admin_password: '' })
  const [jobId, setJobId] = useState(null)
  const [revealed, setRevealed] = useState(null) // captured one-time admin_password

  const installMut = useMutation({
    mutationFn: () => {
      const body = {}
      if (form.title.trim()) body.title = form.title.trim()
      if (form.admin_user.trim()) body.admin_user = form.admin_user.trim()
      if (form.admin_email.trim()) body.admin_email = form.admin_email.trim()
      if (form.admin_password) body.admin_password = form.admin_password
      return post(base, body)
    },
    onSuccess: (job) => { toast.success('WordPress install started', 'This can take a minute or two.'); setRevealed(null); setJobId(job.id) },
    onError: (e) => toast.error('Could not start install', e.message),
  })

  const { data: job, error, refetch } = useQuery({
    queryKey: ['wp-job', username, domain, jobId],
    queryFn: () => get(`${base}/jobs/${jobId}`),
    enabled: jobId != null,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'pending' || s === 'running' ? 3000 : false
    },
  })

  // Capture the one-time admin password the moment it's revealed — the server
  // clears it after the first successful read, so re-fetches return null.
  if (job?.admin_password && revealed == null) setRevealed(job.admin_password)

  const startOver = () => { setJobId(null); setRevealed(null) }

  // ---- Install form (no job yet) ------------------------------------------
  if (jobId == null) {
    return (
      <div className="max-w-2xl">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Download className="h-4 w-4" /> Install WordPress</CardTitle>
            <CardDescription>
              The domain's docroot must be empty. Creates a scoped database automatically, downloads the latest
              WordPress release from wordpress.org, and installs it — the site serves immediately once complete.
            </CardDescription>
          </CardHeader>
          <form onSubmit={(e) => { e.preventDefault(); installMut.mutate() }}>
            <CardContent className="space-y-4">
              <FormField label="Site title" hint={`Defaults to ${domain}`}>
                <Input value={form.title} placeholder={domain}
                  onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} />
              </FormField>
              <FormField label="Admin username" required>
                <Input required value={form.admin_user} placeholder="admin"
                  onChange={(e) => setForm((f) => ({ ...f, admin_user: e.target.value }))} />
              </FormField>
              <FormField label="Admin email" hint={`Defaults to webmaster@${domain}`}>
                <Input type="email" value={form.admin_email} placeholder={`webmaster@${domain}`}
                  onChange={(e) => setForm((f) => ({ ...f, admin_email: e.target.value }))} />
              </FormField>
              <FormField label="Admin password" hint="Leave blank to auto-generate a strong password (shown once when done).">
                <Input type="password" value={form.admin_password} placeholder="Auto-generated if blank"
                  onChange={(e) => setForm((f) => ({ ...f, admin_password: e.target.value }))} />
              </FormField>
            </CardContent>
            <CardFooter>
              <Button type="submit" loading={installMut.isPending} disabled={!form.admin_user.trim()}>
                <Download className="h-4 w-4" /> Install WordPress
              </Button>
            </CardFooter>
          </form>
        </Card>
      </div>
    )
  }

  // ---- Job status ---------------------------------------------------------
  const pct = wpPercent(job)
  // Treat the first-poll gap (job still undefined) as active so the "Start
  // over" footer doesn't flash before the initial status arrives.
  const active = !job || job.status === 'pending' || job.status === 'running'
  const displayPassword = revealed ?? job?.admin_password ?? null

  return (
    <div className="max-w-2xl">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <CardTitle className="flex items-center gap-2">
              {job?.status === 'completed' ? <CheckCircle2 className="h-4 w-4 text-success" />
                : job?.status === 'failed' ? <AlertCircle className="h-4 w-4 text-danger" />
                : <Download className="h-4 w-4" />}
              WordPress install
            </CardTitle>
            {job && <StatusBadge status={job.status} />}
          </div>
          <CardDescription>{job?.progress_message || 'Starting…'}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {error ? (
            <ErrorState error={error} onRetry={refetch} />
          ) : (
            <>
              <ProgressBar value={pct} color={job?.status === 'failed' ? 'bg-danger' : undefined} size="lg" />

              {active && (
                <p className="text-sm text-muted-foreground">
                  Installing… this page updates automatically every few seconds.
                </p>
              )}

              {job?.status === 'failed' && (
                <p className="rounded-btn border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
                  {job.error || 'The install failed.'}
                </p>
              )}

              {job?.status === 'completed' && (
                <div className="space-y-3">
                  <CopyRow label="Admin URL" value={job.admin_url} href={job.admin_url} />
                  <CopyRow label="Admin username" value={job.admin_user} />
                  {displayPassword ? (
                    <>
                      <CopyRow label="Admin password" value={displayPassword} />
                      <p className="text-xs text-warning">
                        This password is shown once — copy it now. It won't be displayed again.
                      </p>
                    </>
                  ) : (
                    <div className="rounded-btn border border-border px-3 py-2">
                      <p className="text-xs text-muted-foreground">Admin password</p>
                      <p className="text-sm text-muted-foreground">
                        Already shown once and not stored — use "Lost password" in wp-admin if you didn't save it.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </CardContent>
        {!active && (
          <CardFooter>
            <Button variant="outline" onClick={startOver}>
              <RotateCcw className="h-4 w-4" /> Start over
            </Button>
          </CardFooter>
        )}
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function DomainDetail() {
  const username = useAccountUsername()
  const { domain } = useParams()

  return (
    <div>
      <Link to="/domains" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> All domains
      </Link>

      <PageHeader title={domain} description="Manage DNS, nameservers, redirects, caching, PHP, security and WordPress for this domain." icon={Globe} />

      <Tabs defaultValue="dns">
        <TabsList>
          <TabsTrigger value="dns">DNS</TabsTrigger>
          <TabsTrigger value="nameservers">Nameservers</TabsTrigger>
          <TabsTrigger value="redirects">Redirects</TabsTrigger>
          <TabsTrigger value="cache">Cache</TabsTrigger>
          <TabsTrigger value="php">PHP</TabsTrigger>
          <TabsTrigger value="security">Security</TabsTrigger>
          <TabsTrigger value="wordpress">WordPress</TabsTrigger>
        </TabsList>

        <TabsContent value="dns"><DnsTab domain={domain} /></TabsContent>
        <TabsContent value="nameservers"><NameserversTab username={username} domain={domain} /></TabsContent>
        <TabsContent value="redirects"><RedirectsTab username={username} domain={domain} /></TabsContent>
        <TabsContent value="cache"><CacheTab username={username} domain={domain} /></TabsContent>
        <TabsContent value="php"><PhpTab username={username} domain={domain} /></TabsContent>
        <TabsContent value="security"><SecurityTab username={username} domain={domain} /></TabsContent>
        <TabsContent value="wordpress"><WordPressTab username={username} domain={domain} /></TabsContent>
      </Tabs>
    </div>
  )
}
