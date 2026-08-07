import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Pause, Play, Trash2, Save, Shield, Gauge, UserCog, FolderOpen, Layers, Ban } from 'lucide-react'
import { get, post, put, patch, del, impersonate as apiImpersonate } from '@/lib/api'
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
import AccountIdentity from '@/pages/admin/AccountIdentity'
import AccountNotes from '@/pages/admin/AccountNotes'
import Processes from '@/pages/customer/Processes'

const ACCOUNT_TABS = [
  ['overview', 'Overview'],
  ['identity', 'Identity'],
  ['domains', 'Domains'],
  ['databases', 'Databases'],
  ['email', 'Email'],
  ['ssl', 'SSL'],
  ['backups', 'Backups'],
  ['apps', 'Apps'],
  ['php-functions', 'PHP Functions'],
  ['processes', 'Processes'],
  ['notes', 'Notes'],
]

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
  // Phase 8 feature 1: "Login as user" -> mint+redeem an impersonation token,
  // then hard-navigate to the customer view as that account.
  const impersonateMut = useMutation({
    mutationFn: () => apiImpersonate(username),
    onSuccess: () => { window.location.assign('/app') },
    onError: (e) => toast.error('Could not log in as user', e.message),
  })

  return (
    <div className="flex flex-wrap gap-2">
      {['active', 'suspended'].includes(account.status) && (
        <Button variant="secondary" size="sm" loading={impersonateMut.isPending} onClick={() => impersonateMut.mutate()}>
          <UserCog className="h-4 w-4" /> Login as user
        </Button>
      )}
      {/* File manager v2: opens the launch endpoint in a new tab, which
          authorizes this admin for the account, audits the access, and opens
          FileBrowser Quantum scoped to the account's home — in its own tab
          so the admin panel stays open. */}
      {['active', 'suspended'].includes(account.status) && (
        <Button
          variant="secondary"
          size="sm"
          onClick={() => window.open(`/api/v1/accounts/${username}/files/launch`, '_blank', 'noopener')}
        >
          <FolderOpen className="h-4 w-4" /> File Manager
        </Button>
      )}
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
        description={<>This permanently removes the Linux user, vhost, databases, mail and DNS zones. This cannot be undone. Cancel and <Link to={`/accounts/${username}?tab=backups`} className="font-medium text-accent hover:underline">review available backups</Link> first if you need a recovery point.</>}
        confirmLabel="Terminate account"
        confirmationText={username}
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

function PlanCard({ username, account }) {
  const qc = useQueryClient()
  const [planId, setPlanId] = useState('')
  const { data: plansData } = useQuery({
    queryKey: ['plans'],
    queryFn: () => get('/api/v1/admin/plans'),
  })
  const plans = plansData?.plans || []
  const currentPlan = plans.find((p) => p.id === account.plan_id)

  const applyMut = useMutation({
    mutationFn: (id) => post(`/api/v1/admin/accounts/${username}/apply-plan/${id}`),
    onSuccess: () => {
      toast.success('Plan applied', 'CPU/RAM/IO/pids, disk quota, usage limits and Redis were all updated.')
      qc.invalidateQueries({ queryKey: ['account', username] })
      qc.invalidateQueries({ queryKey: ['usage-limits', username] })
    },
    onError: (e) => toast.error('Could not apply plan', e.message),
  })

  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Layers className="h-4 w-4" /> Plan</CardTitle></CardHeader>
      <CardContent className="flex items-end gap-3">
        <div className="flex-1 text-sm">
          <div className="text-muted-foreground">Current plan</div>
          <div className="font-medium text-foreground">{currentPlan ? currentPlan.name : 'Custom (no plan applied)'}</div>
        </div>
        <FormField label="Apply plan" className="flex-1">
          <Select value={planId} onChange={(e) => setPlanId(e.target.value)}>
            <option value="">Select a plan…</option>
            {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </FormField>
        <Button loading={applyMut.isPending} disabled={!planId} onClick={() => applyMut.mutate(planId)}>Apply</Button>
      </CardContent>
    </Card>
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

// QA round 2, item 9: admin-only PHP disable_functions overrides, per-account
// or per-domain, layered on the hardened system default
// (daemon/phpdirectives.DEFAULT_DISABLE_FUNCTIONS, applied to the real
// lsphp php.ini files by scripts/install.sh). Deliberately lives only on
// this admin-only page -- never on the customer-facing PHP settings tab.
function FunctionListEditor({ value, onChange, disabled }) {
  return (
    <Input
      value={value.join(', ')}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value.split(',').map((s) => s.trim()).filter(Boolean))}
      placeholder="exec, system, shell_exec"
      className="font-mono text-sm"
    />
  )
}

function PhpFunctionsTab({ username }) {
  const qc = useQueryClient()
  const [selectedDomain, setSelectedDomain] = useState('') // '' = account-wide
  const [draft, setDraft] = useState(null) // list of function names being edited, or null = not editing

  const { data: domainsData } = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })
  const domains = domainsData?.domains || []

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['php-functions', username],
    queryFn: () => get(`/api/v1/admin/accounts/${username}/php-functions`),
    enabled: !!username,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['php-functions', username] })

  const saveMut = useMutation({
    mutationFn: (disable_functions) =>
      put(`/api/v1/admin/accounts/${username}/php-functions`, { domain: selectedDomain || null, disable_functions }),
    onSuccess: () => { toast.success('Override saved'); invalidate(); setDraft(null) },
    onError: (e) => toast.error('Could not save override', e.message),
  })

  const clearMut = useMutation({
    mutationFn: () => del(`/api/v1/admin/accounts/${username}/php-functions${selectedDomain ? `?domain=${encodeURIComponent(selectedDomain)}` : ''}`),
    onSuccess: () => { toast.success('Override cleared — reverted to the scope above'); invalidate(); setDraft(null) },
    onError: (e) => toast.error('Could not clear override', e.message),
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  const scopeOverride = selectedDomain
    ? (data?.domain_overrides || []).find((o) => o.domain === selectedDomain)
    : data?.account_override
  const effective = scopeOverride?.disable_functions ?? (selectedDomain ? null : data?.default_disable_functions) ?? null
  const editing = draft !== null
  const shown = editing ? draft : (scopeOverride?.disable_functions || data?.default_disable_functions || [])

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Ban className="h-4 w-4" /> PHP disabled functions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Hardened server default: <code className="font-mono text-xs">{(data?.default_disable_functions || []).join(', ') || '(none)'}</code>.
          Override it below for this account, or for one specific domain.
        </p>
        <FormField label="Scope">
          <Select value={selectedDomain} onChange={(e) => { setSelectedDomain(e.target.value); setDraft(null) }}>
            <option value="">Account-wide ({username})</option>
            {domains.map((d) => <option key={d.domain} value={d.domain}>{d.domain}</option>)}
          </Select>
        </FormField>
        <FormField
          label="Disabled functions"
          hint={
            scopeOverride
              ? 'This scope has its own override, shown below.'
              : selectedDomain
                ? 'No override for this domain — falls back to the account-wide override (if any), else the server default, shown below.'
                : 'No account-wide override — the server default is shown below.'
          }
        >
          <FunctionListEditor value={shown} onChange={setDraft} disabled={saveMut.isPending || clearMut.isPending} />
        </FormField>
        <div className="flex gap-2">
          <Button
            onClick={() => saveMut.mutate(shown)}
            loading={saveMut.isPending}
          >
            <Save className="h-4 w-4" /> Save override for this scope
          </Button>
          {scopeOverride && (
            <Button variant="secondary" loading={clearMut.isPending} onClick={() => clearMut.mutate()}>
              Clear override
            </Button>
          )}
        </div>
        {effective == null && (
          <p className="text-xs text-muted-foreground">No functions disabled at this scope (fully re-enabled).</p>
        )}
      </CardContent>
    </Card>
  )
}

export default function AccountDetail() {
  const { username } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const validTabs = new Set(ACCOUNT_TABS.map(([value]) => value))
  const requestedTab = searchParams.get('tab')
  const activeTab = validTabs.has(requestedTab) ? requestedTab : 'overview'
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

          <Tabs value={activeTab} onValueChange={(tab) => setSearchParams((prev) => {
            const next = new URLSearchParams(prev)
            if (tab === 'overview') next.delete('tab')
            else next.set('tab', tab)
            return next
          })}>
            <Select
              value={activeTab}
              onChange={(event) => setSearchParams((prev) => {
                const next = new URLSearchParams(prev)
                if (event.target.value === 'overview') next.delete('tab')
                else next.set('tab', event.target.value)
                return next
              })}
              aria-label="Account section"
              className="mb-4 sm:hidden"
            >
              {ACCOUNT_TABS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </Select>
            <TabsList className="hidden sm:flex">
              {ACCOUNT_TABS.map(([value, label]) => <TabsTrigger key={value} value={value}>{label}</TabsTrigger>)}
            </TabsList>

            <TabsContent value="overview" className="space-y-6">
              <Card>
                <CardHeader><CardTitle>Account actions</CardTitle></CardHeader>
                <CardContent><AdminActions username={username} account={account} /></CardContent>
              </Card>
              <PhpAndLimits username={username} account={account} />
              <PlanCard username={username} account={account} />
              <NamespaceCard username={username} />
              <UsageLimitsCard />
            </TabsContent>

            <TabsContent value="identity"><AccountIdentity username={username} account={account} /></TabsContent>
            <TabsContent value="domains"><Domains /></TabsContent>
            <TabsContent value="databases"><Databases /></TabsContent>
            <TabsContent value="email"><Email /></TabsContent>
            <TabsContent value="ssl"><Ssl /></TabsContent>
            <TabsContent value="backups"><Backups /></TabsContent>
            <TabsContent value="apps"><Apps /></TabsContent>
            <TabsContent value="php-functions"><PhpFunctionsTab username={username} /></TabsContent>
            <TabsContent value="processes"><Processes embedded /></TabsContent>
            <TabsContent value="notes"><AccountNotes username={username} /></TabsContent>
          </Tabs>
        </>
      ) : null}
    </div>
  )
}
