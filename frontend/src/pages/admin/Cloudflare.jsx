import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Cloud, Plus, Trash2, Wrench, RefreshCw, Zap, CheckCircle2, AlertCircle,
  Server, Globe, ShieldAlert, Pencil,
} from 'lucide-react'
import { get, post, patch, del } from '@/lib/api'
import { relativeTime, formatDuration } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Switch } from '@/components/ui/Toggle'
import { Input, FormField } from '@/components/ui/Input'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import { ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { toast } from '@/components/ui/Toast'

// A green/red status pill for a boolean check (rails, ranges).
function StatusPill({ ok, label }) {
  return (
    <Badge variant={ok ? 'success' : 'danger'}>
      {ok ? <CheckCircle2 className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />} {label}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// 1. Accounts — pool CRUD
// ---------------------------------------------------------------------------
const EMPTY_ADD = { name: '', api_token: '', account_id: '', max_zones: '800' }

function AccountsTab() {
  const qc = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [addForm, setAddForm] = useState(EMPTY_ADD)
  const [editAccount, setEditAccount] = useState(null) // full row being edited
  const [editForm, setEditForm] = useState({ max_zones: '', active: true })
  const [deleteAccount, setDeleteAccount] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['cf-accounts'],
    queryFn: () => get('/api/v1/cloudflare/accounts'),
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['cf-accounts'] })

  const addMut = useMutation({
    mutationFn: (body) => post('/api/v1/cloudflare/accounts', body),
    onSuccess: () => { toast.success('Cloudflare account added'); invalidate(); setAddOpen(false); setAddForm(EMPTY_ADD) },
    onError: (e) => toast.error('Could not add account', e.message),
  })

  const setMut = useMutation({
    mutationFn: ({ id, body }) => patch(`/api/v1/cloudflare/accounts/${id}`, body),
    onSuccess: () => { toast.success('Account updated'); invalidate(); setEditAccount(null) },
    onError: (e) => toast.error('Could not update account', e.message),
  })

  const testMut = useMutation({
    mutationFn: (id) => post(`/api/v1/cloudflare/accounts/${id}/test`),
    onSuccess: (res) => {
      if (res.error) { toast.error('Token test failed', res.error); return }
      toast.success(
        res.ok ? 'Token is valid' : 'Token test completed',
        `Token valid: ${res.token_valid ? 'yes' : 'no'} · API reachable: ${res.api_ok ? 'yes' : 'no'}`
          + (res.live_zone_count != null ? ` · live zones: ${res.live_zone_count}` : ''),
      )
    },
    onError: (e) => toast.error('Token test failed', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (id) => del(`/api/v1/cloudflare/accounts/${id}`),
    onSuccess: () => { toast.success('Account deleted'); invalidate(); setDeleteAccount(null) },
    onError: (e) => { toast.error('Could not delete account', e.message); setDeleteAccount(null) },
  })

  const openEdit = (row) => {
    setEditForm({ max_zones: String(row.max_zones), active: !!row.active })
    setEditAccount(row)
  }

  const columns = [
    {
      key: 'name', header: 'Name', sortable: true, searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.name}</span>,
    },
    {
      key: 'account_id', header: 'Cloudflare account ID', searchable: true,
      render: (r) => <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{r.account_id}</code>,
    },
    {
      key: 'zone_count', header: 'Zones', align: 'right', sortable: true,
      sortValue: (r) => r.zone_count ?? 0,
      render: (r) => (
        <span className="tabular-nums text-muted-foreground">
          {r.zone_count}/{r.max_zones}
          {r.full && <Badge variant="warning" className="ml-2">Full</Badge>}
        </span>
      ),
    },
    {
      key: 'active', header: 'Active', align: 'center', sortable: true,
      sortValue: (r) => (r.active ? 1 : 0),
      render: (r) => <Badge variant={r.active ? 'success' : 'neutral'}>{r.active ? 'Active' : 'Inactive'}</Badge>,
    },
    {
      key: 'actions', header: '', align: 'right', searchable: false,
      render: (r) => (
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" loading={testMut.isPending && testMut.variables === r.id}
            onClick={() => testMut.mutate(r.id)}>
            <Zap className="h-4 w-4" /> Test
          </Button>
          <Button variant="ghost" size="icon-sm" title="Edit" onClick={() => openEdit(r)}>
            <Pencil className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon-sm" title="Delete" onClick={() => setDeleteAccount(r)}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      {data?.legacy_single_token && (
        <p className="rounded-btn border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-foreground">
          A legacy single-token Cloudflare configuration is in use. Add a pool account below to supersede it —
          existing zones are folded into the pool automatically at startup.
        </p>
      )}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Account pool</CardTitle>
            <CardDescription>
              Cloudflare accounts new zones are distributed across (round-robin by remaining capacity). Tokens are
              write-only — they are never displayed after being added.
            </CardDescription>
          </div>
          <Button className="shrink-0" onClick={() => { setAddForm(EMPTY_ADD); setAddOpen(true) }}>
            <Plus className="h-4 w-4" /> Connect account
          </Button>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={columns}
            data={data?.accounts}
            loading={isLoading}
            error={error}
            onRetry={refetch}
            getRowKey={(r) => r.id}
            filterable
            searchPlaceholder="Search accounts…"
            pageSize={10}
            emptyTitle="No Cloudflare accounts connected"
            emptyDescription="Connect a Cloudflare account so new zones can be created against the pool."
            emptyIcon={Cloud}
            emptyAction={<Button onClick={() => { setAddForm(EMPTY_ADD); setAddOpen(true) }}><Plus className="h-4 w-4" /> Connect account</Button>}
          />
        </CardContent>
      </Card>

      {/* Add account dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Connect a Cloudflare account</DialogTitle>
            <DialogDescription>The API token is verified live against Cloudflare before it is stored (encrypted at rest, never shown again).</DialogDescription>
          </DialogHeader>
          <form onSubmit={(e) => {
            e.preventDefault()
            addMut.mutate({
              name: addForm.name.trim(),
              api_token: addForm.api_token,
              account_id: addForm.account_id.trim(),
              max_zones: Number(addForm.max_zones) || 800,
            })
          }}>
            <DialogBody className="space-y-4">
              <p className="rounded-btn border border-border bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
                Create a token at{' '}
                <a href="https://dash.cloudflare.com/profile/api-tokens" target="_blank" rel="noopener noreferrer"
                  className="font-medium text-primary underline">dash.cloudflare.com → API Tokens</a>{' '}
                with these permissions: <span className="font-medium text-foreground">Zone·Read, Zone·Edit, DNS·Edit,
                Zone Settings·Edit, Cache Purge, Account · Zone·Create</span>. Your{' '}
                <span className="font-medium text-foreground">account ID</span> is on any zone's Overview page
                (right sidebar) or the account home URL.
              </p>
              <FormField label="Name" required hint="A label for this account in the pool.">
                <Input autoFocus value={addForm.name} placeholder="cf-account-1"
                  onChange={(e) => setAddForm((f) => ({ ...f, name: e.target.value }))} required />
              </FormField>
              <FormField label="API token" required hint="Scoped API token, not your Global API Key. Verified before saving.">
                <Input type="password" value={addForm.api_token} placeholder="••••••••••••••••" autoComplete="new-password"
                  onChange={(e) => setAddForm((f) => ({ ...f, api_token: e.target.value }))} required />
              </FormField>
              <FormField label="Cloudflare account ID" required hint="32-character hex id from the Cloudflare dashboard.">
                <Input value={addForm.account_id} placeholder="e.g. 023e105f4ecef8ad9ca31a8372d0c353"
                  onChange={(e) => setAddForm((f) => ({ ...f, account_id: e.target.value }))} required />
              </FormField>
              <FormField label="Max zones" hint="Capacity cap for round-robin assignment.">
                <Input type="number" min="1" value={addForm.max_zones}
                  onChange={(e) => setAddForm((f) => ({ ...f, max_zones: e.target.value }))} />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setAddOpen(false)}>Cancel</Button>
              <Button type="submit" loading={addMut.isPending}>Connect account</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Edit account dialog */}
      <Dialog open={!!editAccount} onOpenChange={(o) => !o && setEditAccount(null)}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Edit {editAccount?.name}</DialogTitle>
            <DialogDescription>Adjust capacity and whether this account receives new zones. The token cannot be changed here.</DialogDescription>
          </DialogHeader>
          <form onSubmit={(e) => {
            e.preventDefault()
            setMut.mutate({ id: editAccount.id, body: { max_zones: Number(editForm.max_zones) || 800, active: editForm.active } })
          }}>
            <DialogBody className="space-y-4">
              <FormField label="Max zones">
                <Input type="number" min="1" value={editForm.max_zones}
                  onChange={(e) => setEditForm((f) => ({ ...f, max_zones: e.target.value }))} />
              </FormField>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-foreground">Active</p>
                  <p className="text-xs text-muted-foreground">Inactive accounts are skipped for new zones.</p>
                </div>
                <Switch checked={editForm.active} onCheckedChange={(v) => setEditForm((f) => ({ ...f, active: v }))} />
              </div>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setEditAccount(null)}>Cancel</Button>
              <Button type="submit" loading={setMut.isPending}>Save changes</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!deleteAccount}
        onOpenChange={(o) => !o && setDeleteAccount(null)}
        title={deleteAccount ? `Delete ${deleteAccount.name}?` : 'Delete account?'}
        description="The account is removed from the pool. This is refused while it still serves zones — revert or migrate those first."
        confirmLabel="Delete account"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(deleteAccount.id)}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// 2. Rails & ranges
// ---------------------------------------------------------------------------
function RailsTab() {
  const qc = useQueryClient()
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['cf-rails'],
    queryFn: () => get('/api/v1/cloudflare/rails'),
  })

  const refreshMut = useMutation({
    mutationFn: (force) => post(`/api/v1/cloudflare/ranges/refresh${force ? '?force=true' : ''}`),
    onSuccess: () => { toast.success('Edge ranges refreshed', 'OpenLiteSpeed and fail2ban are re-synced when the ranges change.'); qc.invalidateQueries({ queryKey: ['cf-rails'] }) },
    onError: (e) => toast.error('Could not refresh ranges', e.message),
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  const rf = data?.ranges_file || {}

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              Real-IP rails {data?.ready
                ? <Badge variant="success">Ready</Badge>
                : <Badge variant="danger">Not ready</Badge>}
            </CardTitle>
            <CardDescription>
              The per-record proxy toggle (orange cloud) unlocks only when the rails are ready — every check below must
              be green so a real visitor IP is always restored before traffic flows through Cloudflare.
            </CardDescription>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button variant="secondary" loading={refreshMut.isPending && refreshMut.variables !== true} onClick={() => refreshMut.mutate(false)}>
              <RefreshCw className="h-4 w-4" /> Refresh ranges
            </Button>
            <Button variant="outline" loading={refreshMut.isPending && refreshMut.variables === true} onClick={() => refreshMut.mutate(true)}>
              Force refresh
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-2">
            <StatusPill ok={data?.ranges_ok} label="Edge ranges fresh" />
            <StatusPill ok={data?.ols_real_ip} label="OLS real-IP" />
            <StatusPill ok={data?.fail2ban_ignoreip} label="fail2ban ignoreip" />
          </div>
          <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
            <div>
              <p className="text-xs text-muted-foreground">Ranges file</p>
              <p className="font-medium text-foreground">{rf.exists ? 'Present' : 'Missing'}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Age</p>
              <p className="font-medium text-foreground">
                {rf.age_seconds != null ? formatDuration(rf.age_seconds) : '—'}
                {rf.stale && <Badge variant="warning" className="ml-2">Stale</Badge>}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">IPv4 ranges</p>
              <p className="font-medium tabular-nums text-foreground">{rf.ipv4_count ?? '—'}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">IPv6 ranges</p>
              <p className="font-medium tabular-nums text-foreground">{rf.ipv6_count ?? '—'}</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3. Zones overview
// ---------------------------------------------------------------------------
function ZonesTab() {
  const qc = useQueryClient()
  const [live, setLive] = useState(false)
  const [purgeOpen, setPurgeOpen] = useState(false)
  const [migrateOpen, setMigrateOpen] = useState(false)
  const [migrateResult, setMigrateResult] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['cf-zones', live],
    queryFn: () => get(`/api/v1/cloudflare/zones${live ? '?live=true' : ''}`),
  })

  const purgeMut = useMutation({
    mutationFn: () => post('/api/v1/cloudflare/bulk-purge', {}),
    onSuccess: (res) => {
      toast.success('Purge complete', `Purged ${res.purged ?? 0} of ${res.total ?? 0} active zone(s).`)
      setPurgeOpen(false)
    },
    onError: (e) => { toast.error('Bulk purge failed', e.message); setPurgeOpen(false) },
  })

  const migrateMut = useMutation({
    mutationFn: () => post('/api/v1/cloudflare/bulk-migrate', {}),
    onSuccess: (res) => {
      setMigrateResult(res)
      qc.invalidateQueries({ queryKey: ['cf-zones'] })
      if (res.stopped_on_error) toast.warning('Migration stopped on error', 'See the per-domain results.')
      else toast.success('Migration run complete', `${res.prepared} prepared, ${res.remaining} remaining.`)
    },
    onError: (e) => { toast.error('Bulk migrate failed', e.message); setMigrateOpen(false) },
    onSettled: () => setMigrateOpen(false),
  })

  const statusVariant = (s) => (s === 'active' ? 'success' : s === 'pending' ? 'warning' : 'neutral')

  const columns = [
    {
      key: 'zone', header: 'Zone', sortable: true, searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.zone}</span>,
    },
    {
      key: 'status', header: 'Status', sortable: true,
      render: (r) => <Badge variant={statusVariant(r.status)}>{r.status}</Badge>,
    },
    { key: 'hosting_account', header: 'Hosting account', sortable: true, searchable: true, render: (r) => r.hosting_account || <span className="text-muted-foreground">—</span> },
    { key: 'cf_account', header: 'CF account', sortable: true, searchable: true, render: (r) => r.cf_account || <span className="text-muted-foreground">legacy</span> },
    {
      key: 'name_servers', header: 'Nameservers',
      render: (r) => <span className="font-mono text-xs text-muted-foreground">{(r.name_servers || []).join(' · ') || '—'}</span>,
    },
    {
      key: 'proxied', header: 'Proxied', align: 'center',
      render: (r) => r.proxied == null
        ? <span className="text-xs text-muted-foreground">—</span>
        : <Badge variant={r.proxied ? 'accent' : 'neutral'}>{r.proxied ? 'Proxied' : 'DNS only'}</Badge>,
    },
    { key: 'ssl_mode', header: 'SSL', align: 'center', render: (r) => r.ssl_mode ? <code className="text-xs">{r.ssl_mode}</code> : <span className="text-xs text-muted-foreground">—</span> },
    {
      key: 'last_purge_at', header: 'Last purge', sortable: true,
      sortValue: (r) => r.last_purge_at || '',
      render: (r) => <span className="text-xs text-muted-foreground">{r.last_purge_at ? relativeTime(r.last_purge_at) : 'never'}</span>,
    },
  ]

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Zones {data?.count != null && <span className="text-muted-foreground">({data.count})</span>}</CardTitle>
            <CardDescription>Every Cloudflare zone across the pool. Enable live status to fetch per-zone proxy + edge SSL mode (one API call per zone).</CardDescription>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Switch checked={live} onCheckedChange={setLive} aria-label="Live status" /> Live status
            </label>
            <Button variant="secondary" onClick={() => setMigrateOpen(true)}>
              <Cloud className="h-4 w-4" /> Migrate all
            </Button>
            <Button variant="outline" onClick={() => setPurgeOpen(true)}>
              <RefreshCw className="h-4 w-4" /> Purge all
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={columns}
            data={data?.zones}
            loading={isLoading}
            error={error}
            onRetry={refetch}
            getRowKey={(r) => r.zone}
            filterable
            searchPlaceholder="Filter zones…"
            pageSize={15}
            initialSort={{ key: 'zone', dir: 'asc' }}
            emptyTitle="No Cloudflare zones"
            emptyDescription="No zones have been enabled on Cloudflare yet."
            emptyIcon={Globe}
          />
        </CardContent>
      </Card>

      <ConfirmDialog
        open={purgeOpen}
        onOpenChange={setPurgeOpen}
        title="Purge cache for all active zones?"
        description="Every active Cloudflare zone has its CDN cache purged. This is best-effort per zone — one failure does not stop the rest."
        confirmLabel="Purge all"
        variant="primary"
        loading={purgeMut.isPending}
        onConfirm={() => purgeMut.mutate()}
      />

      <ConfirmDialog
        open={migrateOpen}
        onOpenChange={setMigrateOpen}
        title="Migrate all PowerDNS-only domains to Cloudflare?"
        description="Each PowerDNS-only zone is created on Cloudflare and seeded, one at a time. Registrar nameservers are NOT changed — each zone stays pending until the customer points them at the assigned pair. Stops at the first failure."
        confirmLabel="Migrate all"
        variant="primary"
        loading={migrateMut.isPending}
        onConfirm={() => migrateMut.mutate()}
      />

      {/* Migration results */}
      <Dialog open={!!migrateResult} onOpenChange={(o) => !o && setMigrateResult(null)}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Migration results</DialogTitle>
            <DialogDescription>
              {migrateResult
                ? `${migrateResult.attempted} attempted · ${migrateResult.prepared} prepared · ${migrateResult.remaining} remaining`
                  + (migrateResult.stopped_on_error ? ' · stopped on error' : '')
                : ''}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {migrateResult?.results?.length ? (
              <div className="space-y-2">
                {migrateResult.results.map((r) => (
                  <div key={r.zone} className="flex items-start justify-between gap-3 rounded-btn border border-border px-3 py-2">
                    <div className="min-w-0">
                      <p className="font-mono text-sm text-foreground">{r.zone}</p>
                      {r.status === 'prepared' && r.name_servers?.length ? (
                        <p className="break-all font-mono text-xs text-muted-foreground">{r.name_servers.join(' · ')}</p>
                      ) : r.error ? (
                        <p className="text-xs text-danger">{r.error}</p>
                      ) : null}
                    </div>
                    <Badge variant={r.status === 'prepared' ? 'success' : 'danger'}>{r.status}</Badge>
                  </div>
                ))}
              </div>
            ) : (
              <p className="py-6 text-center text-sm text-muted-foreground">No PowerDNS-only domains to migrate.</p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setMigrateResult(null)}>Close</Button>
            {migrateResult?.remaining > 0 && (
              <Button loading={migrateMut.isPending} onClick={() => migrateMut.mutate()}>
                <Cloud className="h-4 w-4" /> Continue migration
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 4. Settings — auto-enable + CF-only lockdown
// ---------------------------------------------------------------------------
function SettingsTab() {
  const qc = useQueryClient()
  const [lockConfirm, setLockConfirm] = useState(false)
  const [forceConfirm, setForceConfirm] = useState(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['cf-settings'],
    queryFn: () => get('/api/v1/cloudflare/settings'),
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['cf-settings'] })

  const autoMut = useMutation({
    mutationFn: (auto_enable) => patch('/api/v1/cloudflare/settings', { auto_enable }),
    onSuccess: (_res, v) => { toast.success(v ? 'Auto-enable turned on' : 'Auto-enable turned off'); invalidate() },
    onError: (e) => toast.error('Could not update setting', e.message),
  })

  const lockdownMut = useMutation({
    mutationFn: (body) => patch('/api/v1/cloudflare/lockdown', body),
    onSuccess: (_res, body) => {
      toast.success(body.enabled ? 'CF-only lockdown enabled' : 'CF-only lockdown disabled')
      invalidate(); setLockConfirm(false); setForceConfirm(false)
    },
    onError: (e, body) => {
      // The daemon refuses when a site would go dark ("...would become unreachable...").
      // Offer a second confirm to retry with force.
      if (body?.enabled && !body?.force && /unreachable/i.test(e.message || '')) {
        setLockConfirm(false)
        setForceConfirm(true)
        return
      }
      toast.error('Could not change lockdown', e.message)
      setForceConfirm(false)
    },
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  const lockdownEnabled = !!data?.lockdown_enabled

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Auto-enable Cloudflare for new domains</CardTitle>
            <CardDescription>
              When on (or the server default DNS provider is Cloudflare), a new domain automatically gets a Cloudflare
              zone and the customer sees its nameserver pair immediately — provided a pool account has capacity.
            </CardDescription>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <Badge variant={data?.auto_enable ? 'success' : 'neutral'}>{data?.auto_enable ? 'On' : 'Off'}</Badge>
            <Switch checked={!!data?.auto_enable} disabled={autoMut.isPending}
              onCheckedChange={(v) => autoMut.mutate(v)} aria-label="Toggle auto-enable" />
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Server default DNS provider: <span className="font-medium text-foreground">{data?.default_dns_provider || 'unknown'}</span>
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2"><ShieldAlert className="h-4 w-4" /> Cloudflare-only lockdown</CardTitle>
            <CardDescription>
              Scope direct :80/:443 to Cloudflare edge IPs so the origin cannot be reached off-proxy. SSH and the panel
              stay open. Only safe once every site is behind Cloudflare.
            </CardDescription>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <Badge variant={lockdownEnabled ? 'danger' : 'neutral'}>{lockdownEnabled ? 'Locked down' : 'Open'}</Badge>
            <Switch
              checked={lockdownEnabled}
              disabled={lockdownMut.isPending}
              onCheckedChange={(next) => {
                if (next) setLockConfirm(true)
                else lockdownMut.mutate({ enabled: false })
              }}
              aria-label="Toggle CF-only lockdown"
            />
          </div>
        </CardHeader>
      </Card>

      {/* Enable confirm */}
      <ConfirmDialog
        open={lockConfirm}
        onOpenChange={setLockConfirm}
        title="Enable Cloudflare-only lockdown?"
        description="This blocks direct :80/:443 from non-Cloudflare IPs. SSH and the panel stay open. Only enable if every site is behind Cloudflare (webmail/pma resolve directly and will break otherwise)."
        confirmLabel="Enable lockdown"
        variant="danger"
        loading={lockdownMut.isPending}
        onConfirm={() => lockdownMut.mutate({ enabled: true, confirm: true })}
      />

      {/* Force retry confirm (some domains would be unreachable) */}
      <ConfirmDialog
        open={forceConfirm}
        onOpenChange={setForceConfirm}
        title="Some domains are not behind Cloudflare"
        description="One or more domains are not on an active Cloudflare zone and would become unreachable (webmail/pma also resolve directly to this IP). Force the lockdown anyway only if every site is genuinely behind Cloudflare."
        confirmLabel="Force lockdown"
        variant="danger"
        loading={lockdownMut.isPending}
        onConfirm={() => lockdownMut.mutate({ enabled: true, confirm: true, force: true })}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function Cloudflare() {
  return (
    <div>
      <PageHeader
        title="Cloudflare"
        description="Manage the Cloudflare account pool, real-IP rails, the zone fleet, auto-enable and the CF-only firewall lockdown."
        icon={Cloud}
      />

      <Tabs defaultValue="accounts">
        <TabsList>
          <TabsTrigger value="accounts"><Server className="h-4 w-4" /> Accounts</TabsTrigger>
          <TabsTrigger value="rails"><Wrench className="h-4 w-4" /> Rails &amp; ranges</TabsTrigger>
          <TabsTrigger value="zones"><Globe className="h-4 w-4" /> Zones</TabsTrigger>
          <TabsTrigger value="settings"><ShieldAlert className="h-4 w-4" /> Settings</TabsTrigger>
        </TabsList>

        <TabsContent value="accounts"><AccountsTab /></TabsContent>
        <TabsContent value="rails"><RailsTab /></TabsContent>
        <TabsContent value="zones"><ZonesTab /></TabsContent>
        <TabsContent value="settings"><SettingsTab /></TabsContent>
      </Tabs>
    </div>
  )
}
