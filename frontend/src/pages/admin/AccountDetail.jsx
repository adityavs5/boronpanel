import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Pause, Play, Trash2, Save, Shield, Gauge, UserCog, FolderOpen, Layers, Ban, ChevronDown, Users } from 'lucide-react'
import { get, post, put, patch, del, impersonate as apiImpersonate } from '@/lib/api'
import { formatMB, formatBytes } from '@/lib/utils'
import { useAccountUsername } from '@/hooks/useAccount'
import { PHP_VERSIONS } from '@/config/constants'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import { StyledSelect as Select } from '@/components/ui/StyledSelect'
import { SettingRow } from '@/components/ui/SettingRow'
import { DraftChanges, useDraftSection } from '@/components/ui/DraftChanges'
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from '@/components/ui/DropdownMenu'
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
  ['security', 'Security'],
  ['advanced', 'Advanced'],
]

function AdminActions({ username, account, danger = false }) {
  const navigate = useNavigate()
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
    onSuccess: () => { toast.success('Account terminated'); invalidate(); setConfirm(null); navigate('/accounts', { replace: true }) },
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
      {!danger && account.status === 'active' && (
        <Button variant="secondary" size="sm" loading={impersonateMut.isPending} onClick={() => impersonateMut.mutate()}>
          <UserCog className="h-4 w-4" /> Login as user
        </Button>
      )}
      {/* File manager v2: opens the launch endpoint in a new tab, which
          authorizes this admin for the account, audits the access, and opens
          FileBrowser Quantum scoped to the account's home — in its own tab
          so the admin panel stays open. */}
      {!danger && account.status === 'active' && (
        <Button
          variant="secondary"
          size="sm"
          onClick={() => window.open(`/api/v1/accounts/${username}/files/launch`, '_blank', 'noopener')}
        >
          <FolderOpen className="h-4 w-4" /> File Manager
        </Button>
      )}
      {danger && account.status === 'active' && (
        <Button variant="warning" size="sm" loading={suspendMut.isPending} onClick={() => setConfirm('suspend')}>
          <Pause className="h-4 w-4" /> Suspend
        </Button>
      )}
      {danger && account.status === 'suspended' && (
        <Button variant="success" size="sm" loading={unsuspendMut.isPending} onClick={() => setConfirm('unsuspend')}>
          <Play className="h-4 w-4" /> Unsuspend
        </Button>
      )}
      {danger && ['active', 'suspended', 'error'].includes(account.status) && (
        <Button variant="danger" size="sm" onClick={() => setConfirm('terminate')}>
          <Trash2 className="h-4 w-4" /> Terminate
        </Button>
      )}
      <ConfirmDialog open={confirm === 'suspend' || confirm === 'unsuspend'} onOpenChange={open => !open && setConfirm(null)} title={`${confirm === 'suspend' ? 'Suspend' : 'Unsuspend'} ${username}?`} description={confirm === 'suspend' ? 'Hosting and customer access will be paused. Account files are retained.' : 'Hosting and customer access will resume.'} confirmationText={username} confirmLabel={confirm === 'suspend' ? 'Suspend account' : 'Unsuspend account'} loading={suspendMut.isPending || unsuspendMut.isPending} onConfirm={() => { (confirm === 'suspend' ? suspendMut : unsuspendMut).mutate(undefined, { onSuccess: () => setConfirm(null) }) }} />
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
  const [touchedLimits,setTouchedLimits] = useState({})
  const [limits, setLimits] = useState({
    cpu_cores: account.cpu_cores ?? account.cpu_pct / 100, memory_gb: account.mem_mb / 1024, io_mb: account.io_mb, pids_max: account.pids_max,
  })
  useEffect(() => { setPhpVersion(account.php_version); setLimits({ cpu_cores: account.cpu_cores ?? account.cpu_pct / 100, memory_gb: account.mem_mb / 1024, io_mb: account.io_mb, pids_max: account.pids_max }) }, [account.php_version, account.cpu_cores, account.cpu_pct, account.mem_mb, account.io_mb, account.pids_max])
  const invalidate = () => qc.invalidateQueries({ queryKey: ['account', username] })

  const phpMut = useMutation({
    mutationFn: () => patch(`/api/v1/accounts/${username}/php-version`, { php_version: phpVersion }),
    onSuccess: () => { toast.success('PHP version updated'); invalidate() },
    onError: (e) => toast.error('Could not change PHP version', e.message),
  })
  const limitsMut = useMutation({
    mutationFn: () => patch(`/api/v1/accounts/${username}/limits`, {
      cpu_pct: Math.round(Number(limits.cpu_cores) * 100), mem_mb: Math.round(Number(limits.memory_gb) * 1024), io_mb: Number(limits.io_mb), pids_max: Number(limits.pids_max),
    }),
    onSuccess: () => { toast.success('Limits updated'); invalidate() },
    onError: (e) => toast.error('Could not update resource limits', e.message),
  })

  const originalLimits = {cpu_cores:account.cpu_cores ?? account.cpu_pct/100,memory_gb:account.mem_mb/1024,io_mb:account.io_mb,pids_max:account.pids_max}
  const limitsValid = Number.isInteger(Number(limits.pids_max)) && Number(limits.cpu_cores)>=0.01 && Number(limits.cpu_cores)<=(account.host_cpu_cores??Infinity) && Number(limits.memory_gb)>=0.0625 && Number(limits.memory_gb)<=64 && Number(limits.io_mb)>=1 && Number(limits.pids_max)>=10
  useDraftSection('account-php',{dirty:phpVersion!==account.php_version,busy:phpMut.isPending,label:'PHP version',onSave:()=>phpMut.mutateAsync(),onDiscard:()=>setPhpVersion(account.php_version)})
  useDraftSection('account-limits',{dirty:Object.keys(originalLimits).some(key=>Number(limits[key])!==Number(originalLimits[key])),busy:limitsMut.isPending,disabled:!limitsValid,label:'Resource limits',onSave:()=>limitsMut.mutateAsync(),onDiscard:()=>setLimits(originalLimits)})

  return (
    <div className="account-php-limits grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader><CardTitle>PHP version</CardTitle></CardHeader>
        <CardContent className="flex items-end gap-3">
          <FormField label="Version" className="flex-1">
            <Select value={phpVersion} onChange={(e) => setPhpVersion(e.target.value)}>
              {PHP_VERSIONS.map((v) => <option key={v} value={v}>PHP {v}</option>)}
            </Select>
          </FormField>
          <Button loading={phpMut.isPending} onClick={() => phpMut.mutate()}>Switch</Button>{phpMut.isPending&&<p role="status" className="setting-row-description">Changing PHP version…</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Resource limits</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="account-limit-rows">
            <SettingRow label="CPU cores" error={touchedLimits.cpu_cores&&!(Number(limits.cpu_cores)>=0.01&&Number(limits.cpu_cores)<=(account.host_cpu_cores??Infinity))?`Enter a core count from 0.01 to ${account.host_cpu_cores??'the server maximum'}.`:null} description={`Maximum ${account.host_cpu_cores ?? 'server'} cores available.`}><Input aria-label="CPU cores" type="number" min="0.01" max={account.host_cpu_cores} step="0.01" value={limits.cpu_cores} onBlur={()=>setTouchedLimits(t=>({...t,cpu_cores:true}))} onChange={e=>setLimits(l=>({...l,cpu_cores:e.target.value}))}/></SettingRow>
            <SettingRow label="Memory (GB)" error={touchedLimits.memory_gb&&!(Number(limits.memory_gb)>=0.0625&&Number(limits.memory_gb)<=64)?'Memory must be between 0.0625 and 64 GB.':null} description={`Server RAM: ${account.host_memory_gb?.toFixed(1) ?? '…'} GB. Allocations can overcommit shared RAM.`}><Input aria-label="Memory (GB)" type="number" min="0.0625" step="0.0625" value={limits.memory_gb} onBlur={()=>setTouchedLimits(t=>({...t,memory_gb:true}))} onChange={e=>setLimits(l=>({...l,memory_gb:e.target.value}))}/></SettingRow>
            <SettingRow label="Disk IO (MB/s)" error={touchedLimits.io_mb&&!(Number(limits.io_mb)>=1)?'Enter an IO limit of at least 1 MB/s.':null}><Input aria-label="Disk IO (MB/s)" type="number" min="1" value={limits.io_mb} onBlur={()=>setTouchedLimits(t=>({...t,io_mb:true}))} onChange={e=>setLimits(l=>({...l,io_mb:e.target.value}))}/></SettingRow>
            <SettingRow label="Max processes" error={touchedLimits.pids_max&&!(Number.isInteger(Number(limits.pids_max))&&Number(limits.pids_max)>=10)?'Enter a whole process count of at least 10.':null}><Input aria-label="Max processes" type="number" min="10" value={limits.pids_max} onBlur={()=>setTouchedLimits(t=>({...t,pids_max:true}))} onChange={e=>setLimits(l=>({...l,pids_max:e.target.value}))}/></SettingRow>
          </div>
          {account.configured_cpu_cores > account.host_cpu_cores && <p className="text-sm text-info">The old allocation was {account.configured_cpu_cores} cores. Effective enforcement is capped at this server's {account.host_cpu_cores} cores; save to reconcile the stored limit.</p>}
          {Number(limits.memory_gb) > account.host_memory_gb && <p className="text-sm text-warning">This allocation exceeds total server RAM. Simultaneous usage by accounts can exhaust memory.</p>}
          <Button loading={limitsMut.isPending} disabled={!limitsValid} onClick={() => limitsMut.mutate()}><Save className="h-4 w-4" /> Update limits</Button>{limitsMut.isPending&&<p role="status" className="setting-row-description">Applying resource limits…</p>}
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
      toast.success('Plan applied', 'CPU/RAM/IO/pids, disk quota, usage limits and Redis were all updated.');setPlanId('')
      qc.invalidateQueries({ queryKey: ['account', username] })
      qc.invalidateQueries({ queryKey: ['usage-limits', username] })
    },
    onError: (e) => toast.error('Could not apply plan', e.message),
  })

  useDraftSection('account-plan',{dirty:!!planId,busy:applyMut.isPending,label:'Plan',onSave:()=>applyMut.mutateAsync(planId),onDiscard:()=>setPlanId('')})

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
        <Button loading={applyMut.isPending} disabled={!planId} onClick={() => applyMut.mutate(planId)}>Apply</Button>{applyMut.isPending&&<p role="status" className="setting-row-description">Applying plan settings…</p>}
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
    onError: (e) => toast.error('Could not update account isolation', e.message),
  })
  return (
    <Card>
      <CardHeader><CardTitle>Namespace isolation</CardTitle></CardHeader>
      <CardContent className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Shield className="h-4 w-4" />
          {data ? (data.enabled ? 'Isolated (private mount namespace)' : data.eligible ? 'Not isolated' : 'Not eligible') : 'Loading…'}
        </div>
        <Switch checked={!!data?.enabled} onCheckedChange={(v) => mut.mutate(v)} disabled={!data || mut.isPending} />{mut.isPending&&<span role="status" className="setting-row-description">Updating account isolation…</span>}
      </CardContent>
    </Card>
  )
}

const USAGE_LIMIT_FIELDS = [
  { key: 'bandwidth_limit_gb', apiKey: 'bandwidth_limit_mb', label: 'Bandwidth limit (GB/month)' },
  { key: 'database_limit', label: 'Database limit' },
  { key: 'email_account_limit', label: 'Email account limit' },
  { key: 'subdomain_limit', label: 'Subdomain limit' },
]

function UsageLimitsForm({ username, data }) {
  const qc = useQueryClient()
  const originalForm = {
    bandwidth_limit_gb: data.bandwidth_limit_mb == null ? '' : data.bandwidth_limit_mb / 1024,
    database_limit: data.database_limit ?? '',
    email_account_limit: data.email_account_limit ?? '',
    subdomain_limit: data.subdomain_limit ?? '',
    auto_suspend_at_100: !!data.auto_suspend_at_100,
  }
  const [form, setForm] = useState(originalForm)

  const mut = useMutation({
    mutationFn: (body) => patch(`/api/v1/accounts/${username}/usage-limits`, body),
    onSuccess: () => { toast.success('Usage limits updated'); qc.invalidateQueries({ queryKey: ['usage-limits', username] }) },
    onError: (e) => toast.error('Could not save usage limits', e.message),
  })

  const save = () => {
    const body = {}
    for (const f of USAGE_LIMIT_FIELDS) {
      const apiKey = f.apiKey || f.key
      const current = form[f.key] === '' ? null : Number(form[f.key])
      const apiValue = f.apiKey && current != null ? Math.round(current * 1024) : current
      const original = data[apiKey] ?? null
      if (apiValue !== original) body[apiKey] = apiValue
    }
    if (form.auto_suspend_at_100 !== !!data.auto_suspend_at_100) body.auto_suspend_at_100 = form.auto_suspend_at_100
    if (Object.keys(body).length === 0) { toast.info('No changes to save'); return }
    return mut.mutateAsync(body)
  }
  const valid=USAGE_LIMIT_FIELDS.every(f=>form[f.key]===''||(Number.isFinite(Number(form[f.key]))&&Number(form[f.key])>=1&&(f.apiKey||Number.isInteger(Number(form[f.key])))))
  useDraftSection('account-usage-limits',{dirty:Object.keys(originalForm).some(key=>String(form[key])!==String(originalForm[key])),busy:mut.isPending,disabled:!valid,label:'Usage limits',onSave:save,onDiscard:()=>setForm(originalForm)})

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
      <Button loading={mut.isPending} disabled={!valid} onClick={()=>save()?.catch(()=>{})}><Save className="h-4 w-4" /> Save usage limits</Button>{mut.isPending&&<p role="status" className="setting-row-description">Saving usage limits…</p>}
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
    <Card className="account-usage-limits">
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

  useDraftSection('php-functions',{dirty:draft!==null,busy:saveMut.isPending,label:'PHP function override',onSave:()=>saveMut.mutateAsync(draft),onDiscard:()=>setDraft(null)})

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

function AccountSummary({username,account}) {
  const {data:usage,error}=useQuery({queryKey:['usage',username],queryFn:()=>get(`/api/v1/accounts/${username}/usage`),refetchInterval:15000,retry:false})
  const {data:plans}=useQuery({queryKey:['plans'],queryFn:()=>get('/api/v1/admin/plans'),retry:false})
  const previous=useRef(null),[cores,setCores]=useState(null)
  const counters=usage?.resources
  useEffect(()=>{
    const last=previous.current
    if(counters?.sampled_at&&last?.username===username){
      const seconds=(Date.parse(counters.sampled_at)-Date.parse(last.sampled_at))/1000
      if(seconds>0&&counters.cpu_usage_usec!=null&&last.cpu_usage_usec!=null)setCores(Math.max(0,counters.cpu_usage_usec-last.cpu_usage_usec)/(seconds*1000000))
    }
    previous.current=counters?{...counters,username}:null
  },[counters?.sampled_at,username])
  const disk=usage?.current?.disk_total_bytes,diskLimit=account.quota_hard_mb*1048576
  const bandwidth=usage?.bandwidth_month_to_date_bytes,bandwidthLimit=counters?.bandwidth_limit_bytes
  const memory=counters?.memory_current_bytes,memoryLimit=counters?.memory_limit_bytes
  const meters=[
    {label:'Disk',value:disk==null?'—':formatBytes(disk),pct:diskLimit>0&&disk!=null?disk/diskLimit*100:null},
    {label:'Bandwidth',value:bandwidth==null?'—':formatBytes(bandwidth),pct:bandwidthLimit&&bandwidth!=null?bandwidth/bandwidthLimit*100:null},
    {label:'CPU',value:error?'—':cores==null?'Collecting…':`${cores.toFixed(2)} cores`,pct:cores!=null&&counters?.cpu_limit_cores?cores/counters.cpu_limit_cores*100:null},
    {label:'Memory',value:memory==null?'—':formatBytes(memory),pct:memoryLimit&&memory!=null?memory/memoryLimit*100:null},
    {label:'Inodes',value:usage?.current?.inode_count?.toLocaleString()??'—',pct:null},
  ]
  const plan=(plans?.plans||[]).find(row=>row.id===account.plan_id)
  return <section className="account-summary" aria-label="Account summary">
    <PageHeader title={username} description="Hosting account settings and resource usage." icon={Users}><AdminActions username={username} account={account}/></PageHeader>
    <div className="account-facts"><StatusBadge status={account.status}/><span>Plan: <strong>{plan?.name||account.plan_name||(account.plan_id?'Loading…':'Custom')}</strong></span><span>Primary: <strong>{account.primary_domain||'None'}</strong></span><span>UID <strong>{account.uid}</strong></span><span>PHP <strong>{account.php_version}</strong></span></div>
    <div className="account-usage">{meters.map(meter=><div className="account-meter" key={meter.label}><div><span>{meter.label}</span><strong>{meter.value}</strong></div>{meter.pct!=null&&<div className="account-meter-track" role="progressbar" aria-label={`${meter.label} usage`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.min(100,meter.pct))}><span style={{width:`${Math.min(100,Math.max(0,meter.pct))}%`}}/></div>}</div>)}</div>
    {error&&<p className="setting-row-description" role="status">Usage unavailable. Statistics will retry on the next refresh.</p>}
  </section>
}

function AccountDetailContent() {
  const navigate = useNavigate()
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

  useEffect(() => { if (account?.status === 'terminated') { toast.info('This account has been terminated'); navigate('/accounts', { replace: true }) } }, [account?.status, navigate])

  return (
    <div className="reference-page">
      <Link to="/accounts" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> All accounts
      </Link>

      {isLoading ? (
        <CardSkeleton />
      ) : error ? (
        <ErrorState error={error} onRetry={refetch} />
      ) : account ? (
        <>
          <AccountSummary username={username} account={account} />

          <Tabs className="account-tabs" value={activeTab} onValueChange={(tab) => setSearchParams((prev) => {
            const next = new URLSearchParams(prev)
            if (tab === 'overview') next.delete('tab')
            else next.set('tab', tab)
            return next
          })}>
            <TabsList className="account-primary-tabs">
              {['overview','domains','email','databases','apps','backups','security','advanced'].map(value=><TabsTrigger key={value} value={value}>{ACCOUNT_TABS.find(([key])=>key===value)[1]}</TabsTrigger>)}
              <DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" aria-label="More account sections">More <ChevronDown className="h-4 w-4"/></Button></DropdownMenuTrigger><DropdownMenuContent>{ACCOUNT_TABS.filter(([value])=>['identity','ssl','php-functions','processes','notes'].includes(value)).map(([value,label])=><DropdownMenuItem key={value} onSelect={()=>setSearchParams(prev=>{const next=new URLSearchParams(prev);next.set('tab',value);return next})}>{label}</DropdownMenuItem>)}</DropdownMenuContent></DropdownMenu>
            </TabsList>

            <TabsContent value="overview" className="account-overview">
              <PhpAndLimits username={username} account={account} />
              <PlanCard username={username} account={account} />
              <NamespaceCard username={username} />
              <UsageLimitsCard />
            </TabsContent>

            <TabsContent value="security" className="space-y-4"><NamespaceCard username={username}/><div className="flex flex-wrap gap-2"><Button variant="secondary" onClick={()=>setSearchParams({tab:'ssl'})}>SSL</Button><Button variant="secondary" onClick={()=>setSearchParams({tab:'processes'})}>Processes</Button></div></TabsContent>
            <TabsContent value="advanced" className="space-y-4"><AccountIdentity username={username} account={account}/><div className="flex flex-wrap gap-2">{[['php-functions','PHP Functions'],['notes','Notes']].map(([tab,label])=><Button variant="secondary" key={tab} onClick={()=>setSearchParams({tab})}>{label}</Button>)}</div></TabsContent>
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
          <section className="danger-zone" aria-label="Danger zone"><h2>Danger zone</h2><p>Pause hosting or permanently remove {username}. Saved backups should be reviewed before termination.</p><AdminActions username={username} account={account} danger/></section>
        </>
      ) : null}
    </div>
  )
}

export default function AccountDetail(){return <DraftChanges><AccountDetailContent/></DraftChanges>}
