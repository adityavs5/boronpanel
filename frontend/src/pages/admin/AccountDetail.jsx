import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Pause, Play, Trash2, Save, Shield, Gauge } from 'lucide-react'
import { get, post, patch } from '@/lib/api'
import { formatMB } from '@/lib/utils'
import { useAccountUsername } from '@/hooks/useAccount'
import { PHP_VERSIONS } from '@/config/constants'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Switch } from '@/components/ui/Toggle'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'

import Domains from '@/pages/customer/Domains'
import Databases from '@/pages/customer/Databases'
import Email from '@/pages/customer/Email'
import Ssl from '@/pages/customer/Ssl'
import Backups from '@/pages/customer/Backups'
import Apps from '@/pages/customer/Apps'

function AdminActions({ username, account }) {
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState(null) // 'terminate' | null
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['account', username] })
    qc.invalidateQueries({ queryKey: ['accounts'] })
  }
  const act = (action) =>
    useMutation({
      mutationFn: () => post(`/api/v1/accounts/${username}/${action}`),
      onSuccess: () => { toast.success(`Account ${action}ed`); invalidate() },
      onError: (e) => toast.error(`Could not ${action}`, e.message),
    })
  const suspendMut = act('suspend')
  const unsuspendMut = act('unsuspend')
  const terminateMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${username}/terminate`),
    onSuccess: () => { toast.success('Account terminated'); invalidate(); setConfirm(null) },
    onError: (e) => { toast.error('Could not terminate', e.message); setConfirm(null) },
  })

  return (
    <div className="flex flex-wrap gap-2">
      {account.status === 'active' && (
        <Button variant="warning" size="sm" loading={suspendMut.isPending} onClick={() => suspendMut.mutate()}>
          <Pause className="h-4 w-4" /> Suspend
        </Button>
      )}
      {account.status === 'suspended' && (
        <Button variant="success" size="sm" loading={unsuspendMut.isPending} onClick={() => unsuspendMut.mutate()}>
          <Play className="h-4 w-4" /> Unsuspend
        </Button>
      )}
      {['active', 'suspended', 'error'].includes(account.status) && (
        <Button variant="danger" size="sm" onClick={() => setConfirm('terminate')}>
          <Trash2 className="h-4 w-4" /> Terminate
        </Button>
      )}
      <ConfirmDialog
        open={confirm === 'terminate'}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={`Terminate ${username}?`}
        description="This permanently removes the Linux user, vhost, databases, mail and DNS zones. This cannot be undone."
        confirmLabel="Terminate account"
        loading={terminateMut.isPending}
        onConfirm={() => terminateMut.mutate()}
      />
    </div>
  )
}

function PhpAndLimits({ username, account }) {
  const qc = useQueryClient()
  const [phpVersion, setPhpVersion] = useState(account.php_version)
  const [limits, setLimits] = useState({
    cpu_pct: account.cpu_pct, mem_mb: account.mem_mb, io_mb: account.io_mb, pids_max: account.pids_max,
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['account', username] })

  const phpMut = useMutation({
    mutationFn: () => patch(`/api/v1/accounts/${username}/php-version`, { php_version: phpVersion }),
    onSuccess: () => { toast.success('PHP version updated'); invalidate() },
    onError: (e) => toast.error('Failed', e.message),
  })
  const limitsMut = useMutation({
    mutationFn: () => patch(`/api/v1/accounts/${username}/limits`, {
      cpu_pct: Number(limits.cpu_pct), mem_mb: Number(limits.mem_mb), io_mb: Number(limits.io_mb), pids_max: Number(limits.pids_max),
    }),
    onSuccess: () => { toast.success('Limits updated'); invalidate() },
    onError: (e) => toast.error('Failed', e.message),
  })

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader><CardTitle>PHP version</CardTitle></CardHeader>
        <CardContent className="flex items-end gap-3">
          <FormField label="Version" className="flex-1">
            <Select value={phpVersion} onChange={(e) => setPhpVersion(e.target.value)}>
              {PHP_VERSIONS.map((v) => <option key={v} value={v}>PHP {v}</option>)}
            </Select>
          </FormField>
          <Button loading={phpMut.isPending} onClick={() => phpMut.mutate()}>Switch</Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Resource limits</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <FormField label="CPU %"><Input type="number" min="1" max="100" value={limits.cpu_pct} onChange={(e) => setLimits((l) => ({ ...l, cpu_pct: e.target.value }))} /></FormField>
            <FormField label="Memory (MB)"><Input type="number" min="64" value={limits.mem_mb} onChange={(e) => setLimits((l) => ({ ...l, mem_mb: e.target.value }))} /></FormField>
            <FormField label="Disk IO (MB/s)"><Input type="number" min="1" value={limits.io_mb} onChange={(e) => setLimits((l) => ({ ...l, io_mb: e.target.value }))} /></FormField>
            <FormField label="Max processes"><Input type="number" min="10" value={limits.pids_max} onChange={(e) => setLimits((l) => ({ ...l, pids_max: e.target.value }))} /></FormField>
          </div>
          <Button loading={limitsMut.isPending} onClick={() => limitsMut.mutate()}><Save className="h-4 w-4" /> Update limits</Button>
        </CardContent>
      </Card>
    </div>
  )
}

function NamespaceCard({ username }) {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['namespace', username], queryFn: () => get(`/api/v1/accounts/${username}/namespace`) })
  const mut = useMutation({
    mutationFn: (enabled) => patch(`/api/v1/accounts/${username}/namespace`, { enabled }),
    onSuccess: () => { toast.success('Namespace updated'); qc.invalidateQueries({ queryKey: ['namespace', username] }) },
    onError: (e) => toast.error('Failed', e.message),
  })
  return (
    <Card>
      <CardHeader><CardTitle>Namespace isolation</CardTitle></CardHeader>
      <CardContent className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Shield className="h-4 w-4" />
          {data ? (data.enabled ? 'Isolated (private mount namespace)' : data.eligible ? 'Not isolated' : 'Not eligible') : 'Loading…'}
        </div>
        <Switch checked={!!data?.enabled} onCheckedChange={(v) => mut.mutate(v)} disabled={!data} />
      </CardContent>
    </Card>
  )
}

const USAGE_LIMIT_FIELDS = [
  { key: 'bandwidth_limit_mb', label: 'Bandwidth limit (MB/month)' },
  { key: 'database_limit', label: 'Database limit' },
  { key: 'email_account_limit', label: 'Email account limit' },
  { key: 'subdomain_limit', label: 'Subdomain limit' },
]

function UsageLimitsForm({ username, data }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    bandwidth_limit_mb: data.bandwidth_limit_mb ?? '',
    database_limit: data.database_limit ?? '',
    email_account_limit: data.email_account_limit ?? '',
    subdomain_limit: data.subdomain_limit ?? '',
    auto_suspend_at_100: !!data.auto_suspend_at_100,
  })

  const mut = useMutation({
    mutationFn: (body) => patch(`/api/v1/accounts/${username}/usage-limits`, body),
    onSuccess: () => { toast.success('Usage limits updated'); qc.invalidateQueries({ queryKey: ['usage-limits', username] }) },
    onError: (e) => toast.error('Failed', e.message),
  })

  const save = () => {
    const body = {}
    for (const f of USAGE_LIMIT_FIELDS) {
      const current = form[f.key] === '' ? null : Number(form[f.key])
      const original = data[f.key] ?? null
      if (current !== original) body[f.key] = current
    }
    if (form.auto_suspend_at_100 !== !!data.auto_suspend_at_100) body.auto_suspend_at_100 = form.auto_suspend_at_100
    if (Object.keys(body).length === 0) { toast.info('No changes to save'); return }
    mut.mutate(body)
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {USAGE_LIMIT_FIELDS.map((f) => (
          <FormField key={f.key} label={f.label} hint="Leave blank to leave untracked">
            <Input
              type="number"
              min="1"
              placeholder="Not tracked"
              value={form[f.key]}
              onChange={(e) => setForm((s) => ({ ...s, [f.key]: e.target.value }))}
            />
          </FormField>
        ))}
      </div>
      <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
        <div className="text-sm">
          <div className="font-medium text-foreground">Auto-suspend at 100%</div>
          <div className="text-muted-foreground">Suspend this account when any tracked limit is fully reached.</div>
        </div>
        <Switch
          checked={form.auto_suspend_at_100}
          onCheckedChange={(v) => setForm((s) => ({ ...s, auto_suspend_at_100: v }))}
        />
      </div>
      <Button loading={mut.isPending} onClick={save}><Save className="h-4 w-4" /> Save usage limits</Button>
    </div>
  )
}

function UsageLimitsCard() {
  const username = useAccountUsername()
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['usage-limits', username],
    queryFn: () => get(`/api/v1/accounts/${username}/usage-limits`),
    enabled: !!username,
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Gauge className="h-4 w-4" /> Usage limits</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <CenteredSpinner label="Loading usage limits…" />
        ) : error ? (
          <ErrorState error={error} onRetry={refetch} />
        ) : data ? (
          <UsageLimitsForm key={data.updated_at || 'new'} username={username} data={data} />
        ) : null}
      </CardContent>
    </Card>
  )
}

export default function AccountDetail() {
  const { username } = useParams()
  const { data: account, isLoading, error, refetch } = useQuery({
    queryKey: ['account', username],
    queryFn: () => get(`/api/v1/accounts/${username}`),
    enabled: !!username,
  })

  return (
    <div>
      <Link to="/accounts" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> All accounts
      </Link>

      {isLoading ? (
        <CardSkeleton />
      ) : error ? (
        <ErrorState error={error} onRetry={refetch} />
      ) : account ? (
        <>
          <PageHeader title={username} description={`uid ${account.uid} · PHP ${account.php_version} · quota ${formatMB(account.quota_hard_mb)}`}>
            <StatusBadge status={account.status} />
          </PageHeader>

          <Tabs defaultValue="overview">
            <TabsList>
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="domains">Domains</TabsTrigger>
              <TabsTrigger value="databases">Databases</TabsTrigger>
              <TabsTrigger value="email">Email</TabsTrigger>
              <TabsTrigger value="ssl">SSL</TabsTrigger>
              <TabsTrigger value="backups">Backups</TabsTrigger>
              <TabsTrigger value="apps">Apps</TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="space-y-6">
              <Card>
                <CardHeader><CardTitle>Account actions</CardTitle></CardHeader>
                <CardContent><AdminActions username={username} account={account} /></CardContent>
              </Card>
              <PhpAndLimits username={username} account={account} />
              <NamespaceCard username={username} />
              <UsageLimitsCard />
            </TabsContent>

            <TabsContent value="domains"><Domains /></TabsContent>
            <TabsContent value="databases"><Databases /></TabsContent>
            <TabsContent value="email"><Email /></TabsContent>
            <TabsContent value="ssl"><Ssl /></TabsContent>
            <TabsContent value="backups"><Backups /></TabsContent>
            <TabsContent value="apps"><Apps /></TabsContent>
          </Tabs>
        </>
      ) : null}
    </div>
  )
}
