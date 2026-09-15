import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip as RTooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import {
  ArrowLeft, Globe, Plus, Pencil, Trash2, Save, RefreshCw,
  ShieldOff, Ban, Lock, Users, Network, ShieldCheck,
  Server, Copy, ExternalLink, CheckCircle2, AlertCircle, Download, RotateCcw, Cloud,
  Construction, Asterisk, FileWarning, Eye, KeyRound, BarChart3, Play, Wrench,
} from 'lucide-react'
import { get, post, put, patch, del } from '@/lib/api'
import { relativeTime, copyToClipboard, formatBytes } from '@/lib/utils'
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
import { Badge } from '@/components/ui/Badge'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { ErrorState, EmptyState } from '@/components/ui/States'
import { ProgressBar } from '@/components/ui/Progress'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { useAccountUsername } from '@/hooks/useAccount'

const DNS_TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SRV', 'CAA']
// Record types Cloudflare can proxy (orange cloud).
const PROXYABLE_TYPES = new Set(['A', 'AAAA', 'CNAME'])
const linesToList = (text) => (text || '').split('\n').map((s) => s.trim()).filter(Boolean)

// ---------------------------------------------------------------------------
// DNS — Cloudflare provider card (docs/PLAN-cloudflare.md Phase 1)
// ---------------------------------------------------------------------------
function NameserverPair({ nameservers }) {
  return (
    <div className="space-y-2">
      {(nameservers || []).map((ns) => (
        <div key={ns} className="flex items-center justify-between rounded-md border border-border bg-muted/40 px-3 py-2">
          <span className="font-mono text-sm">{ns}</span>
          <Button variant="ghost" size="icon-sm" title="Copy"
            onClick={() => { copyToClipboard(ns); toast.success('Copied', ns) }}>
            <Copy className="h-4 w-4" />
          </Button>
        </div>
      ))}
    </div>
  )
}

function CloudflareZoneCard({ domain, cloudflare, proxyAvailable, onChanged }) {
  const base = `/api/v1/dns/zones/${domain}/cloudflare`
  const [enableOpen, setEnableOpen] = useState(false)
  const [revertOpen, setRevertOpen] = useState(false)

  const status = cloudflare?.status // undefined | 'pending' | 'active'

  const enableMut = useMutation({
    mutationFn: () => post(`${base}/enable`),
    onSuccess: () => { setEnableOpen(false); onChanged() },
    onError: (e) => toast.error('Could not enable Cloudflare', e.message),
  })
  const checkMut = useMutation({
    mutationFn: () => post(`${base}/check`),
    onSuccess: (res) => {
      if (res.activated) toast.success('Zone is now active on Cloudflare')
      else toast.info('Still pending', 'Cloudflare has not seen the nameserver change yet. Registrar updates can take a while to propagate.')
      onChanged()
    },
    onError: (e) => toast.error('Activation check failed', e.message),
  })
  const revertMut = useMutation({
    mutationFn: () => post(`${base}/disable`),
    onSuccess: (res) => { setRevertOpen(false); toast.success('Reverted to local DNS', res.message); onChanged() },
    onError: (e) => { setRevertOpen(false); toast.error('Could not revert', e.message) },
  })
  const purgeMut = useMutation({
    mutationFn: () => post(`${base}/purge`),
    onSuccess: () => toast.success('Cloudflare cache purged'),
    onError: (e) => toast.error('Could not purge cache', e.message),
  })
  const proxyAllMut = useMutation({
    mutationFn: () => post(`${base}/proxy-all`),
    onSuccess: (res) => { toast.success('Records proxied', `${res.proxied_records ?? 0} record(s) now proxied through Cloudflare.`); onChanged() },
    onError: (e) => toast.error('Could not proxy records', e.message),
  })

  return (
    <Card className="mb-4">
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2"><Cloud className="h-4 w-4" /> Cloudflare</CardTitle>
          <CardDescription>
            {status === 'active'
              ? 'This zone is served by Cloudflare (anycast DNS). Records are managed here as usual.'
              : status === 'pending'
                ? 'Waiting for the registrar nameserver change. Local DNS keeps serving until activation.'
                : 'Serve this zone from Cloudflare’s global network: anycast DNS, CDN caching and DDoS protection.'}
          </CardDescription>
        </div>
        {status === 'active' && <Badge variant="success">Active</Badge>}
        {status === 'pending' && <Badge variant="warning">Pending activation</Badge>}
        {!status && <Badge variant="neutral">Local DNS</Badge>}
      </CardHeader>
      <CardContent className="space-y-3">
        {status === 'pending' && (
          <>
            <p className="text-sm text-muted-foreground">
              Set these two nameservers for <span className="font-medium text-foreground">{domain}</span> at your
              domain registrar. Activation is detected automatically (checked every 15 minutes).
            </p>
            <NameserverPair nameservers={cloudflare.name_servers} />
          </>
        )}
        {status === 'active' && (
          <p className="text-sm text-muted-foreground">
            Nameservers: <span className="font-mono text-xs">{(cloudflare.name_servers || []).join(' · ')}</span>
          </p>
        )}
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        {!status && (
          <Button size="sm" onClick={() => setEnableOpen(true)}>
            <Cloud className="h-4 w-4" /> Enable Cloudflare
          </Button>
        )}
        {status === 'pending' && (
          <Button size="sm" variant="secondary" loading={checkMut.isPending} onClick={() => checkMut.mutate()}>
            <RefreshCw className="h-4 w-4" /> Check activation
          </Button>
        )}
        {status === 'active' && (
          <Button size="sm" variant="secondary" loading={purgeMut.isPending} onClick={() => purgeMut.mutate()}>
            <RefreshCw className="h-4 w-4" /> Purge cache
          </Button>
        )}
        {status === 'active' && (
          <Button
            size="sm"
            variant="secondary"
            loading={proxyAllMut.isPending}
            disabled={!proxyAvailable}
            title={proxyAvailable ? undefined : 'Enable Cloudflare real-IP rails first (admin → Cloudflare)'}
            onClick={() => proxyAllMut.mutate()}
          >
            <Cloud className="h-4 w-4" /> Proxy all records
          </Button>
        )}
        {status && (
          <Button size="sm" variant="outline" onClick={() => setRevertOpen(true)}>
            <RotateCcw className="h-4 w-4" /> Revert to local DNS
          </Button>
        )}
      </CardFooter>

      <Dialog open={enableOpen} onOpenChange={setEnableOpen}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Enable Cloudflare for {domain}?</DialogTitle>
            <DialogDescription>
              The zone is created on Cloudflare and seeded with a copy of its current records. Nothing changes for
              visitors until you point the domain's registrar nameservers at the pair Cloudflare assigns — local DNS
              keeps serving until then, and you can revert at any time.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setEnableOpen(false)}>Cancel</Button>
            <Button loading={enableMut.isPending} onClick={() => enableMut.mutate()}>Enable Cloudflare</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={revertOpen}
        onOpenChange={setRevertOpen}
        title="Revert to local DNS?"
        description={
          status === 'active'
            ? 'Records are synced back from Cloudflare, the Cloudflare zone is removed, and this server serves DNS again. Remember to point the registrar nameservers back to the defaults afterwards.'
            : 'The pending Cloudflare zone is removed. Local DNS was never interrupted.'
        }
        confirmLabel="Revert to local DNS"
        variant="danger"
        loading={revertMut.isPending}
        onConfirm={() => revertMut.mutate()}
      />
    </Card>
  )
}

// ---------------------------------------------------------------------------
// DNS
// ---------------------------------------------------------------------------
export function DnsTab({ domain }) {
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
    return { subdomain, type: r.type, ttl: r.ttl, values, proxied: !!r.proxied, _key: `${subdomain}|${r.type}` }
  })

  // Top-level gate from dns.list_records: the record proxy toggle only unlocks
  // once the admin has the Cloudflare real-IP rails green (docs/PLAN-cloudflare.md SS1.5/SS1.7).
  const proxyAvailable = !!data?.proxy_available

  const invalidate = () => qc.invalidateQueries({ queryKey: ['dns-records', domain] })

  const proxyMut = useMutation({
    // Toggling the orange cloud re-sends the whole rrset with the new proxied
    // flag — the same set-record endpoint used to save records.
    mutationFn: ({ row, proxied }) =>
      put(`/api/v1/dns/zones/${domain}/records`, {
        domain,
        subdomain: row.subdomain,
        type: row.type,
        values: row.values,
        ttl: row.ttl,
        proxied,
      }),
    onSuccess: (_res, { proxied }) => { toast.success(proxied ? 'Proxying enabled' : 'Proxying disabled'); invalidate() },
    onError: (e) => toast.error('Could not update proxy status', e.message),
  })

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

  // A DNS zone (local or Cloudflare) only ever exists for the exact domain
  // dns.create_zone was called on — never for a subdomain/addon that just
  // lives inside another domain's zone. Cloudflare in particular can only
  // be enabled per whole zone, so a subdomain never gets its own "Enable
  // Cloudflare" control; it rides along automatically once its parent
  // zone is active (docs/PLAN-cloudflare.md).
  // This return must stay BELOW every hook above: `managed === false` only
  // becomes true once the query resolves (second render), so returning above
  // any hook changes the hook count between renders — React error #300, and
  // with no error boundary in this app, a blank page.
  if (data && data.managed === false) {
    return (
      <Card>
        <CardContent>
          <EmptyState
            icon={Network}
            title={data.parent_zone ? 'Managed under a different zone' : 'No DNS zone for this domain'}
            description={
              data.parent_zone ? (
                <>
                  <span className="font-mono">{domain}</span> doesn't have its own DNS zone — its records (and any
                  Cloudflare proxying) live inside <span className="font-medium text-foreground">{data.parent_zone}</span>'s
                  zone. Open that domain's DNS tab to manage records or enable Cloudflare; this subdomain will follow
                  automatically.
                </>
              ) : (
                <>
                  <span className="font-mono">{domain}</span> isn't a Boron-managed DNS zone, and no managed zone
                  covers it as a subdomain either. DNS (and Cloudflare) is only available for a domain Boron
                  manages as its own zone — if this domain's DNS is hosted elsewhere, add a record there pointing at
                  this server instead.
                </>
              )
            }
          />
        </CardContent>
      </Card>
    )
  }

  const columns = [
    { key: 'subdomain', header: 'Name', sortable: true, searchable: true, render: (r) => <span className="font-mono text-xs">{r.subdomain}</span> },
    { key: 'type', header: 'Type', sortable: true, render: (r) => <span className="font-mono text-xs">{r.type}</span> },
    { key: 'ttl', header: 'TTL', sortable: true, render: (r) => r.ttl },
    { key: 'values', header: 'Value(s)', render: (r) => <div className="whitespace-pre-line break-all font-mono text-xs">{r.values.join('\n')}</div> },
    {
      key: 'proxied', header: 'Proxy', align: 'center', searchable: false, render: (r) => {
        const eligible = PROXYABLE_TYPES.has(r.type)
        const canToggle = eligible && proxyAvailable
        const pending = proxyMut.isPending && proxyMut.variables?.row?._key === r._key
        const title = !eligible
          ? 'Only A, AAAA and CNAME records can be proxied'
          : !proxyAvailable
            ? 'Enable Cloudflare real-IP rails first (admin → Cloudflare)'
            : r.proxied
              ? 'Proxied through Cloudflare — click for DNS only'
              : 'DNS only — click to proxy through Cloudflare'
        return (
          <Button
            variant="ghost"
            size="icon-sm"
            title={title}
            loading={pending}
            disabled={!canToggle || pending}
            className={eligible && r.proxied ? 'text-orange-500 hover:text-orange-600' : 'text-muted-foreground'}
            onClick={() => proxyMut.mutate({ row: r, proxied: !r.proxied })}
          >
            {!pending && <Cloud className="h-4 w-4" fill={eligible && r.proxied ? 'currentColor' : 'none'} />}
          </Button>
        )
      },
    },
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
      {data && (
        <CloudflareZoneCard
          domain={domain}
          cloudflare={data.cloudflare}
          proxyAvailable={proxyAvailable}
          onChanged={() => { invalidate(); qc.invalidateQueries({ queryKey: ['nameservers'] }) }}
        />
      )}

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
        description={`This restores Boron-managed defaults (ns1.${domain} / ns2.${domain}) pointing at this server.`}
        confirmLabel="Reset to defaults"
        variant="danger"
        loading={resetMut.isPending}
        onConfirm={() => resetMut.mutate()}
      />
    </Card>
  )
}

export function NameserversTab({ username, domain }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['nameservers', username, domain],
    queryFn: () => get(`/api/v1/accounts/${username}/domains/${domain}/nameservers`),
    enabled: !!username && !!domain,
  })

  const nameservers = data?.nameservers || []
  const glue = data?.glue || {}

  // Cloudflare zones: the NS pair is assigned by Cloudflare, not edited here
  // (docs/PLAN-cloudflare.md SS1.8) — show it instead of the glue form.
  if (data?.provider === 'cloudflare') {
    return (
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="flex items-center gap-2"><Cloud className="h-4 w-4" /> Cloudflare nameservers</CardTitle>
            <CardDescription>
              This zone is on Cloudflare — set these at your registrar. Custom nameservers require reverting
              to local DNS first (DNS tab).
            </CardDescription>
          </div>
          {data.cloudflare_status === 'active'
            ? <Badge variant="success">Active</Badge>
            : <Badge variant="warning">Pending activation</Badge>}
        </CardHeader>
        <CardContent>
          <NameserverPair nameservers={nameservers} />
        </CardContent>
      </Card>
    )
  }
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
export function RedirectsTab({ username, domain }) {
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
// Forwarding (whole-domain 301/302) — Phase 8 feature 4
// ---------------------------------------------------------------------------
export function ForwardingTab({ username, domain }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/domains/${domain}/forwarding`
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['forwarding', username, domain],
    queryFn: () => get(base),
    enabled: !!username && !!domain,
  })
  const fwd = data?.forwarding || null
  const [seeded, setSeeded] = useState(false)
  const [form, setForm] = useState({ target_url: '', status_code: '301', keep_path: true })
  if (!seeded && data) {
    if (fwd) setForm({ target_url: fwd.target_url, status_code: String(fwd.status_code), keep_path: fwd.keep_path })
    setSeeded(true)
  }
  const invalidate = () => qc.invalidateQueries({ queryKey: ['forwarding', username, domain] })

  const saveMut = useMutation({
    mutationFn: () => put(base, { target_url: form.target_url.trim(), status_code: Number(form.status_code), keep_path: form.keep_path }),
    onSuccess: () => { toast.success('Forwarding saved', `${domain} now redirects.`); invalidate() },
    onError: (e) => toast.error('Could not save forwarding', e.message),
  })
  const removeMut = useMutation({
    mutationFn: () => del(base),
    onSuccess: () => { toast.success('Forwarding removed'); setForm({ target_url: '', status_code: '301', keep_path: true }); invalidate() },
    onError: (e) => toast.error('Could not remove forwarding', e.message),
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  return (
    <div className="max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><ExternalLink className="h-4 w-4" /> Domain forwarding</CardTitle>
          <CardDescription>
            Redirect this ENTIRE domain to another URL. Replaces normal serving (the ACME challenge path is kept, so
            SSL still works). For redirecting a single path, use the Redirects tab instead.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {fwd && (
            <p className="rounded-btn border border-accent/40 bg-accent/10 px-3 py-2 text-sm text-foreground">
              Currently forwarding to <span className="font-mono">{fwd.target_url}</span> ({fwd.status_code}
              {fwd.keep_path ? ', keeping path' : ''}).
            </p>
          )}
          <FormField label="Destination URL" required hint="Absolute http(s) URL, e.g. https://example.org">
            <Input required type="url" value={form.target_url} placeholder="https://example.org"
              onChange={(e) => setForm((f) => ({ ...f, target_url: e.target.value }))} />
          </FormField>
          <div className="grid grid-cols-2 gap-4">
            <FormField label="Redirect type">
              <Select value={form.status_code} onChange={(e) => setForm((f) => ({ ...f, status_code: e.target.value }))}>
                <option value="301">301 (permanent)</option>
                <option value="302">302 (temporary)</option>
              </Select>
            </FormField>
            <FormField label="Keep request path" hint="Append the original URI to the target.">
              <div className="flex h-10 items-center">
                <Switch checked={form.keep_path} onCheckedChange={(v) => setForm((f) => ({ ...f, keep_path: v }))} />
              </div>
            </FormField>
          </div>
        </CardContent>
        <CardFooter className="flex gap-2">
          <Button loading={saveMut.isPending} disabled={!form.target_url.trim()} onClick={() => saveMut.mutate()}>
            <Save className="h-4 w-4" /> {fwd ? 'Update forwarding' : 'Enable forwarding'}
          </Button>
          {fwd && (
            <Button variant="outline" loading={removeMut.isPending} onClick={() => removeMut.mutate()}>
              <Trash2 className="h-4 w-4" /> Remove forwarding
            </Button>
          )}
        </CardFooter>
      </Card>
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

export function CacheTab({ username, domain }) {
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
export function PhpTab({ username, domain }) {
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

export function SecurityTab({ username, domain }) {
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

// QA round 2, item 2: WordPress management actions, reusing the exact same
// allowlisted WP-CLI backend (daemon/wpcli.py) DevTools.jsx's account-level
// WP-CLI tab already calls -- kept as a separate constant here (not
// imported from DevTools.jsx, a page module) rather than shared, matching
// this codebase's existing per-page-module convention for such lists.
const WP_ACTIONS = [
  { value: 'core_update', label: 'Update WordPress core' },
  { value: 'core_check_update', label: 'Check for core updates' },
  { value: 'plugin_list', label: 'List plugins' },
  { value: 'plugin_update', label: 'Update plugin', fields: ['name_or_all'] },
  { value: 'plugin_activate', label: 'Activate plugin', fields: ['name'] },
  { value: 'plugin_deactivate', label: 'Deactivate plugin', fields: ['name'] },
  { value: 'theme_list', label: 'List themes' },
  { value: 'theme_update', label: 'Update theme', fields: ['name_or_all'] },
  { value: 'theme_activate', label: 'Activate theme', fields: ['name'] },
  { value: 'theme_deactivate', label: 'Deactivate theme', fields: ['name'] },
  { value: 'user_reset_password', label: 'Reset admin password', fields: ['user'] },
  { value: 'cache_flush', label: 'Flush cache' },
  { value: 'search_replace', label: 'Search-replace (preview first)', fields: ['search', 'replace'] },
  { value: 'maintenance_on', label: 'Enable maintenance mode' },
  { value: 'maintenance_off', label: 'Disable maintenance mode' },
]

// A live-updating panel for a WP-CLI CommandRun job, scoped to this domain.
function WpCliRunOutput({ username, domain, jobId }) {
  const { data: job } = useQuery({
    queryKey: ['wp-action-run', username, domain, jobId],
    queryFn: () => get(`/api/v1/accounts/${username}/domains/${domain}/wordpress/actions/runs/${jobId}`),
    enabled: jobId != null,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s === 'pending' || s === 'running' ? 1500 : false
    },
  })
  if (jobId == null) return null
  const output = [(job?.stdout || ''), (job?.stderr || '')].filter(Boolean).join('\n')
  return (
    <div className="mt-3 space-y-2">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-mono text-xs text-muted-foreground">{job?.command}</span>
        {job && <StatusBadge status={job.status} />}
        {job?.exit_code != null && <span className="text-xs text-muted-foreground">exit {job.exit_code}</span>}
      </div>
      {job?.revealed_secret && (
        <p className="rounded-btn border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
          New password (shown once): <span className="font-mono font-semibold">{job.revealed_secret}</span>
        </p>
      )}
      <pre className="max-h-64 overflow-auto rounded-card border border-border bg-[#0b1120] p-3 font-mono text-xs text-slate-200">
        {output || (job?.status === 'running' || job?.status === 'pending' ? 'Running…' : job?.error || '(no output)')}
      </pre>
    </div>
  )
}

// Management card for one detected WordPress install at this domain (root
// or a subdirectory, item 3) -- admin URL/version up front, WP-CLI actions
// (update core/plugins/themes, reset admin password, cache flush,
// maintenance, search-replace) tucked behind "Manage" so a domain with
// several installs doesn't show a wall of forms at once.
function WordPressInstallCard({ username, domain, install }) {
  const base = `/api/v1/accounts/${username}/domains/${domain}/wordpress`
  const [action, setAction] = useState('plugin_list')
  const [fields, setFields] = useState({ name: '', all: false, user: 'admin', search: '', replace: '', preview: true })
  const [jobId, setJobId] = useState(null)
  const [open, setOpen] = useState(false)
  const spec = WP_ACTIONS.find((a) => a.value === action)
  const adminUrl = `https://${domain}${install.path ? `/${install.path}` : ''}/wp-admin/`

  const runMut = useMutation({
    mutationFn: () => {
      const body = { action, path: install.path || '' }
      if (spec.fields?.includes('name_or_all')) { if (fields.all) body.all = true; else body.name = fields.name }
      if (spec.fields?.includes('name')) body.name = fields.name
      if (spec.fields?.includes('user')) body.user = fields.user
      if (spec.fields?.includes('search')) { body.search = fields.search; body.replace = fields.replace; body.preview = fields.preview }
      return post(`${base}/actions`, body)
    },
    onSuccess: (job) => { setJobId(job.id); toast.success('WP-CLI command started') },
    onError: (e) => toast.error('Could not run WP-CLI', e.message),
  })

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-success" />
            {install.path ? `${domain}/${install.path}` : domain}
          </CardTitle>
          {install.wp_version && <Badge variant="accent">WP {install.wp_version}</Badge>}
        </div>
        <CardDescription>{install.path ? `Installed in the "${install.path}" subdirectory` : 'Installed at the domain root'}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <CopyRow label="Admin URL" value={adminUrl} href={adminUrl} />
        <Button variant="outline" size="sm" onClick={() => setOpen((v) => !v)}>
          <Wrench className="h-4 w-4" /> {open ? 'Hide WP-CLI actions' : 'Manage (WP-CLI actions)'}
        </Button>
        {open && (
          <div className="space-y-4 rounded-card border border-border p-4">
            <FormField label="Command">
              <Select value={action} onChange={(e) => setAction(e.target.value)}>
                {WP_ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
              </Select>
            </FormField>
            {spec.fields?.includes('name_or_all') && (
              <div className="flex items-end gap-3">
                <FormField label="Plugin/theme slug" className="flex-1">
                  <Input value={fields.name} disabled={fields.all} onChange={(e) => setFields((f) => ({ ...f, name: e.target.value }))} placeholder="akismet" />
                </FormField>
                <FormField label="All"><div className="flex h-10 items-center"><Switch checked={fields.all} onCheckedChange={(v) => setFields((f) => ({ ...f, all: v }))} /></div></FormField>
              </div>
            )}
            {spec.fields?.includes('name') && (
              <FormField label="Plugin/theme slug"><Input value={fields.name} onChange={(e) => setFields((f) => ({ ...f, name: e.target.value }))} placeholder="akismet" /></FormField>
            )}
            {spec.fields?.includes('user') && (
              <FormField label="User login"><Input value={fields.user} onChange={(e) => setFields((f) => ({ ...f, user: e.target.value }))} placeholder="admin" /></FormField>
            )}
            {spec.fields?.includes('search') && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <FormField label="Search for"><Input value={fields.search} onChange={(e) => setFields((f) => ({ ...f, search: e.target.value }))} placeholder="http://old.com" /></FormField>
                <FormField label="Replace with"><Input value={fields.replace} onChange={(e) => setFields((f) => ({ ...f, replace: e.target.value }))} placeholder="https://new.com" /></FormField>
                <FormField label="Preview only (dry-run)"><div className="flex h-10 items-center"><Switch checked={fields.preview} onCheckedChange={(v) => setFields((f) => ({ ...f, preview: v }))} /></div></FormField>
              </div>
            )}
            <Button loading={runMut.isPending} onClick={() => runMut.mutate()}><Play className="h-4 w-4" /> Run command</Button>
            <WpCliRunOutput username={username} domain={domain} jobId={jobId} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// The install form + async job status (unchanged behavior from before this
// item, apart from the new "Subdirectory" field) -- used both for the very
// first install at a domain and for adding another one alongside existing
// installs (item 3).
function WordPressInstallForm({ username, domain, suggestSubdirectory, onDone }) {
  const base = `/api/v1/accounts/${username}/domains/${domain}/wordpress`
  const [form, setForm] = useState({ path: '', title: '', admin_user: 'admin', admin_email: '', admin_password: '' })
  const [jobId, setJobId] = useState(null)
  const [revealed, setRevealed] = useState(null) // captured one-time admin_password

  const installMut = useMutation({
    mutationFn: () => {
      const body = {}
      if (form.path.trim()) body.path = form.path.trim()
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

  const finish = () => { setJobId(null); setRevealed(null); onDone?.() }

  // ---- Install form (no job yet) ------------------------------------------
  if (jobId == null) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Download className="h-4 w-4" /> Install WordPress</CardTitle>
          <CardDescription>
            {suggestSubdirectory
              ? 'Install another copy of WordPress in a subdirectory of this domain — the target directory must be empty and gets its own database, tracked separately from your other installs.'
              : "The domain's docroot must be empty. Creates a scoped database automatically, downloads the latest WordPress release from wordpress.org, and installs it — the site serves immediately once complete."}
          </CardDescription>
        </CardHeader>
        <form onSubmit={(e) => { e.preventDefault(); installMut.mutate() }}>
          <CardContent className="space-y-4">
            <FormField label="Subdirectory" hint={`Leave blank to install at the domain root (${domain}). Enter e.g. "blog" for ${domain}/blog.`}>
              <Input value={form.path} placeholder="blog"
                onChange={(e) => setForm((f) => ({ ...f, path: e.target.value }))} />
            </FormField>
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
    )
  }

  // ---- Job status ---------------------------------------------------------
  const pct = wpPercent(job)
  // Treat the first-poll gap (job still undefined) as active so the "Done"
  // footer doesn't flash before the initial status arrives.
  const active = !job || job.status === 'pending' || job.status === 'running'
  const displayPassword = revealed ?? job?.admin_password ?? null

  return (
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
          <Button variant="outline" onClick={finish}>
            <RotateCcw className="h-4 w-4" /> Done
          </Button>
        </CardFooter>
      )}
    </Card>
  )
}

// QA round 2, items 2+3: shows the management section (admin URL/version +
// WP-CLI actions) once WordPress is detected at this domain, for every
// install found (root and/or any subdirectories) -- reusing the existing
// WP-CLI backend wholesale. Falls back to the install form when nothing is
// detected yet, same as before this item.
export function WordPressTab({ username, domain }) {
  const base = `/api/v1/accounts/${username}/domains/${domain}/wordpress`
  const qc = useQueryClient()
  const [showInstallForm, setShowInstallForm] = useState(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['wp-installs', username, domain],
    queryFn: () => get(`${base}/installs`),
    enabled: !!username && !!domain,
  })
  const installs = data?.installs || []

  const refreshInstalls = () => { setShowInstallForm(false); qc.invalidateQueries({ queryKey: ['wp-installs', username, domain] }) }

  if (isLoading) return <div className="max-w-2xl"><CardSkeleton /></div>
  if (error) return <div className="max-w-2xl"><ErrorState error={error} onRetry={refetch} /></div>

  return (
    <div className="max-w-2xl space-y-4">
      {installs.map((install) => (
        <WordPressInstallCard key={install.id} username={username} domain={domain} install={install} />
      ))}

      {(installs.length === 0 || showInstallForm) && (
        <WordPressInstallForm
          username={username}
          domain={domain}
          suggestSubdirectory={installs.length > 0}
          onDone={refreshInstalls}
        />
      )}

      {installs.length > 0 && !showInstallForm && (
        <Button variant="outline" onClick={() => setShowInstallForm(true)}>
          <Plus className="h-4 w-4" /> Install another WordPress
        </Button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Maintenance mode (missing-features batch, goal feature 2)
// ---------------------------------------------------------------------------
const AUTO_DISABLE_OPTIONS = [
  { value: '', label: 'Manual (leave on until I turn it off)' },
  { value: '60', label: '1 hour' },
  { value: '240', label: '4 hours' },
  { value: '1440', label: '24 hours' },
]

export function MaintenanceTab({ username, domain }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/domains/${domain}/maintenance`
  const key = ['maintenance', username, domain]
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: key, queryFn: () => get(base), enabled: !!username && !!domain,
  })
  const [seeded, setSeeded] = useState(false)
  const [form, setForm] = useState({ title: '', message: '', estimated_time: '', auto_disable_minutes: '' })
  if (!seeded && data) {
    setForm({
      title: data.title || '', message: data.message || '', estimated_time: data.estimated_time || '',
      auto_disable_minutes: data.auto_disable_minutes ? String(data.auto_disable_minutes) : '',
    })
    setSeeded(true)
  }
  const invalidate = () => qc.invalidateQueries({ queryKey: key })

  const setMut = useMutation({
    mutationFn: (enabled) => patch(base, {
      enabled, title: form.title, message: form.message, estimated_time: form.estimated_time,
      auto_disable_minutes: form.auto_disable_minutes ? Number(form.auto_disable_minutes) : null,
    }),
    onSuccess: (_r, enabled) => { toast.success(enabled ? 'Maintenance mode enabled' : 'Maintenance mode disabled'); invalidate() },
    onError: (e) => toast.error('Could not update maintenance mode', e.message),
  })
  const regenMut = useMutation({
    mutationFn: () => patch(base, { enabled: data?.enabled ?? true, title: form.title, message: form.message, estimated_time: form.estimated_time, regenerate_token: true }),
    onSuccess: () => { toast.success('Bypass link regenerated'); invalidate() },
    onError: (e) => toast.error('Could not regenerate bypass link', e.message),
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  const bypassUrl = data?.bypass_token ? `https://${domain}/?${data.bypass_query_param}=${data.bypass_token}` : null

  return (
    <div className="max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Construction className="h-4 w-4" /> Maintenance mode</CardTitle>
          <CardDescription>
            Show visitors a 503 "under maintenance" page instead of the live site. Certbot renewals keep working while
            enabled. Use the bypass link below to preview the real site.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {data?.enabled && (
            <p className="rounded-btn border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-foreground">
              Maintenance mode is <strong>ON</strong> — visitors see a 503 page.
              {data.auto_disable_at && <> Auto-disables at {new Date(data.auto_disable_at).toLocaleString()}.</>}
            </p>
          )}
          <FormField label="Title">
            <Input value={form.title} onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} placeholder="We'll be right back" />
          </FormField>
          <FormField label="Message">
            <Textarea rows={3} value={form.message} onChange={(e) => setForm((f) => ({ ...f, message: e.target.value }))}
              placeholder="This site is currently undergoing scheduled maintenance." />
          </FormField>
          <div className="grid grid-cols-2 gap-4">
            <FormField label="Estimated time" hint="Shown to visitors, e.g. '30 minutes'">
              <Input value={form.estimated_time} onChange={(e) => setForm((f) => ({ ...f, estimated_time: e.target.value }))} placeholder="30 minutes" />
            </FormField>
            <FormField label="Auto-disable after">
              <Select value={form.auto_disable_minutes} onChange={(e) => setForm((f) => ({ ...f, auto_disable_minutes: e.target.value }))}>
                {AUTO_DISABLE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </Select>
            </FormField>
          </div>
          {bypassUrl && (
            <FormField label="Bypass link" hint="Visit this URL once to preview the real site while maintenance mode is on.">
              <div className="flex items-center gap-2">
                <Input readOnly value={bypassUrl} className="font-mono text-xs" />
                <Button variant="ghost" size="icon-sm" title="Copy" onClick={() => { copyToClipboard(bypassUrl); toast.success('Copied') }}>
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
            </FormField>
          )}
        </CardContent>
        <CardFooter className="flex flex-wrap gap-2">
          {!data?.enabled ? (
            <Button loading={setMut.isPending} onClick={() => setMut.mutate(true)}>
              <Construction className="h-4 w-4" /> Enable maintenance mode
            </Button>
          ) : (
            <Button variant="outline" loading={setMut.isPending} onClick={() => setMut.mutate(false)}>
              <ShieldCheck className="h-4 w-4" /> Disable maintenance mode
            </Button>
          )}
          {data?.enabled && (
            <Button variant="ghost" loading={setMut.isPending} onClick={() => setMut.mutate(true)}>
              <Save className="h-4 w-4" /> Save changes
            </Button>
          )}
          {data?.bypass_token && (
            <Button variant="ghost" loading={regenMut.isPending} onClick={() => regenMut.mutate()}>
              <KeyRound className="h-4 w-4" /> Regenerate bypass link
            </Button>
          )}
        </CardFooter>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Wildcard domains (missing-features batch, goal feature 3)
// ---------------------------------------------------------------------------
export function WildcardTab({ username, domain }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/domains/${domain}/wildcard`
  const key = ['wildcard', username, domain]
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: key, queryFn: () => get(base), enabled: !!username && !!domain,
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: key })

  const setMut = useMutation({
    mutationFn: (enabled) => patch(base, { enabled }),
    onSuccess: (_r, enabled) => { toast.success(enabled ? 'Wildcard routing enabled' : 'Wildcard routing disabled'); invalidate() },
    onError: (e) => toast.error('Could not update wildcard routing', e.message),
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  return (
    <div className="max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Asterisk className="h-4 w-4" /> Wildcard subdomains</CardTitle>
          <CardDescription>
            Route any undefined subdomain of <span className="font-mono">{domain}</span> (e.g. anything.{domain}) to
            this domain's own docroot — no need to add each subdomain individually.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!data?.zone_managed && (
            <p className="rounded-btn border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-foreground">
              This domain's DNS zone isn't managed by Boron yet, so a wildcard A record can't be created here.
              Manage this domain's DNS first (DNS tab), or create <span className="font-mono">*.{domain}</span>{' '}
              manually with your DNS provider.
            </p>
          )}
          <div className="flex items-center justify-between rounded-btn border border-border px-3 py-2.5">
            <div>
              <p className="text-sm font-medium text-foreground">Wildcard routing</p>
              <p className="text-xs text-muted-foreground">*.{domain} → this domain's docroot</p>
            </div>
            <Switch checked={!!data?.enabled} disabled={!data?.zone_managed && !data?.enabled}
              onCheckedChange={(v) => setMut.mutate(v)} />
          </div>
          {data?.enabled && !data?.ssl_is_wildcard && (
            <p className="rounded-btn border border-info/40 bg-info/10 px-3 py-2 text-sm text-foreground">
              Wildcard routing is on, but there's no wildcard SSL certificate for this domain yet — HTTPS requests to
              undefined subdomains will show a certificate warning. Issue a wildcard certificate from the SSL page.
            </p>
          )}
          {data?.existing_subdomains?.length > 0 && (
            <FormField label="Existing explicit subdomains" hint="These keep serving their own content — wildcard routing only applies to undefined subdomains.">
              <div className="flex flex-wrap gap-1.5">
                {data.existing_subdomains.map((s) => <Badge key={s} variant="outline">{s}</Badge>)}
              </div>
            </FormField>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Custom error pages (missing-features batch, goal feature 4)
// ---------------------------------------------------------------------------
const ERROR_CODE_LABELS = { 403: 'Forbidden', 404: 'Not Found', 500: 'Server Error', 503: 'Unavailable' }

export function ErrorPagesTab({ username, domain }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/domains/${domain}/error-pages`
  const listKey = ['error-pages', username, domain]
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: listKey, queryFn: () => get(base), enabled: !!username && !!domain,
  })
  const [activeCode, setActiveCode] = useState(404)
  const [content, setContent] = useState(null)
  const [loadedCode, setLoadedCode] = useState(null)

  const pageQ = useQuery({
    queryKey: [...listKey, activeCode],
    queryFn: () => get(`${base}/${activeCode}`),
    enabled: !!username && !!domain,
  })
  if (pageQ.data && loadedCode !== activeCode) {
    setContent(pageQ.data.content || '')
    setLoadedCode(activeCode)
  }

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: listKey })
    setLoadedCode(null)
  }
  const saveMut = useMutation({
    mutationFn: () => put(`${base}/${activeCode}`, { content }),
    onSuccess: () => { toast.success(`Custom ${activeCode} page saved`); invalidate() },
    onError: (e) => toast.error('Could not save page', e.message),
  })
  const resetMut = useMutation({
    mutationFn: () => del(`${base}/${activeCode}`),
    onSuccess: () => { toast.success(`Reverted to the default ${activeCode} page`); invalidate() },
    onError: (e) => toast.error('Could not reset page', e.message),
  })

  const preview = () => {
    const blob = new Blob([content ?? ''], { type: 'text/html' })
    const url = URL.createObjectURL(blob)
    window.open(url, '_blank', 'noopener,noreferrer')
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }

  if (isLoading) return <CardSkeleton />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  const pages = data?.pages || []
  const activeInfo = pages.find((p) => p.code === activeCode)

  return (
    <div className="max-w-3xl">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><FileWarning className="h-4 w-4" /> Custom error pages</CardTitle>
          <CardDescription>
            Replace the default Boron-branded 403/404/500/503 pages with your own HTML, stored under this domain's
            own error_pages/ directory.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {pages.map((p) => (
              <button key={p.code} type="button" onClick={() => setActiveCode(p.code)}
                className={`rounded-btn border px-3 py-1.5 text-sm font-medium transition-colors ${
                  activeCode === p.code ? 'border-accent bg-accent/10 text-accent' : 'border-border text-muted-foreground hover:text-foreground'
                }`}>
                {p.code} {ERROR_CODE_LABELS[p.code]}
                {p.has_custom && <Badge variant="accent" className="ml-1.5">custom</Badge>}
              </button>
            ))}
          </div>
          <FormField label={`${activeCode} page HTML`} hint={activeInfo?.has_custom ? 'Custom page in use.' : 'Using the default Boron-branded page — save to customize.'}>
            <Textarea rows={12} className="font-mono text-xs" value={content ?? ''}
              onChange={(e) => setContent(e.target.value)} placeholder="<html>...</html>" />
          </FormField>
        </CardContent>
        <CardFooter className="flex flex-wrap gap-2">
          <Button loading={saveMut.isPending} disabled={!content?.trim()} onClick={() => saveMut.mutate()}>
            <Save className="h-4 w-4" /> Save {activeCode} page
          </Button>
          <Button variant="outline" onClick={preview}><Eye className="h-4 w-4" /> Preview</Button>
          {activeInfo?.has_custom && (
            <Button variant="ghost" loading={resetMut.isPending} onClick={() => resetMut.mutate()}>
              <RotateCcw className="h-4 w-4" /> Revert to default
            </Button>
          )}
        </CardFooter>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Site statistics (missing-features batch, goal feature 6)
// ---------------------------------------------------------------------------
function StatsChartCard({ title, data, dataKey, color, formatY }) {
  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">{title}</CardTitle></CardHeader>
      <CardContent>
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={`g-stats-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={color} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--border))" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: 'rgb(var(--muted-fg))' }} tickLine={false} axisLine={false} minTickGap={40} />
              <YAxis tick={{ fontSize: 11, fill: 'rgb(var(--muted-fg))' }} tickLine={false} axisLine={false} width={48} tickFormatter={formatY} />
              <RTooltip
                contentStyle={{ background: 'rgb(var(--card))', border: '1px solid rgb(var(--border))', borderRadius: 8, fontSize: 12 }}
                labelStyle={{ color: 'rgb(var(--muted-fg))' }}
                formatter={(v) => [formatY ? formatY(v) : v, title]}
              />
              <Area type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2} fill={`url(#g-stats-${dataKey})`} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  )
}

function TopListCard({ title, rows, labelKey, icon: Icon }) {
  return (
    <Card>
      <CardHeader><CardTitle className="text-sm flex items-center gap-2">{Icon && <Icon className="h-4 w-4" />} {title}</CardTitle></CardHeader>
      <CardContent>
        {!rows?.length ? (
          <p className="text-sm text-muted-foreground">No data yet.</p>
        ) : (
          <div className="space-y-1.5">
            {rows.map((r) => (
              <div key={r[labelKey]} className="flex items-center justify-between text-sm">
                <span className="truncate font-mono text-xs text-foreground" title={r[labelKey]}>{r[labelKey]}</span>
                <span className="shrink-0 text-muted-foreground">{r.count.toLocaleString()}</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function StatsTab({ username, domain }) {
  const [period, setPeriod] = useState('daily')
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['sitestats', username, domain, period],
    queryFn: () => get(`/api/v1/accounts/${username}/domains/${domain}/stats`, { params: { period } }),
    enabled: !!username && !!domain,
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  const buckets = data?.buckets || []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex gap-4 text-sm text-muted-foreground">
          <span><strong className="text-foreground">{(data?.total_pageviews ?? 0).toLocaleString()}</strong> pageviews</span>
          <span><strong className="text-foreground">{formatBytes(data?.total_bytes_served ?? 0)}</strong> served</span>
          <span><strong className="text-foreground">{(data?.total_unique_visitors_approx ?? 0).toLocaleString()}</strong> visitors</span>
        </div>
        <Select value={period} onChange={(e) => setPeriod(e.target.value)} className="w-40">
          <option value="daily">Daily (30d)</option>
          <option value="weekly">Weekly (12w)</option>
          <option value="monthly">Monthly (12mo)</option>
        </Select>
      </div>

      {!data?.geoip_configured && (
        <p className="rounded-btn border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          Top-countries breakdown needs a MaxMind GeoLite2 license key, configured by an admin.
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <StatsChartCard title="Pageviews" data={buckets} dataKey="pageviews" color="#1FBED6" formatY={(v) => v} />
        <StatsChartCard title="Bandwidth" data={buckets} dataKey="bytes_served" color="#10B981" formatY={(v) => formatBytes(v)} />
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <TopListCard title="Top pages" rows={data?.top_pages} labelKey="path" icon={BarChart3} />
        <TopListCard title="Top referrers" rows={data?.top_referrers} labelKey="referrer" />
        <TopListCard title="Top countries" rows={data?.top_countries} labelKey="country_code" />
      </div>
    </div>
  )
}

export default function DomainDetail() {
  const username = useAccountUsername()
  const { domain } = useParams()
  const { data, isLoading } = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })
  const item = data?.domains?.find((entry) => entry.domain === domain)

  return (
    <div className="domain-settings-page">
      <Link to="/domains" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> All domains
      </Link>
      <PageHeader title={domain} description="Domain setup and advanced hosting settings." icon={Globe} />
      <div className="settings-columns">
        <Card>
          <CardHeader><div><CardTitle>Domain information</CardTitle><CardDescription>The website location and current service state.</CardDescription></div></CardHeader>
          <CardContent>
            <dl className="settings-list">
              <div><dt>Domain name</dt><dd>{domain}</dd></div>
              <div><dt>Type</dt><dd>{isLoading ? 'Loading…' : item?.kind || 'Domain'}</dd></div>
              <div><dt>Status</dt><dd><StatusBadge status={item?.status || 'active'} /></dd></div>
              <div><dt>Document root</dt><dd className="font-mono">{item?.docroot || 'Loading…'}</dd></div>
              <div><dt>PHP version</dt><dd>{item?.php_version ? `PHP ${item.php_version}` : 'Account default'}</dd></div>
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><div><CardTitle>Advanced settings</CardTitle><CardDescription>Changes here affect how this domain is served.</CardDescription></div></CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">Document-root and PHP changes are available from the dedicated PHP settings screen. Account suspension and disk or bandwidth limits are controlled by the account administrator.</p>
            <div className="flex flex-wrap gap-2">
              <Button asChild><Link to="/php">PHP & document root settings</Link></Button>
              <Button asChild variant="secondary"><Link to="/domains">Add another domain</Link></Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
