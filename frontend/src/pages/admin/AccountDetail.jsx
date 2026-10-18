import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Pause, Play, Trash2, Save, UserCog, FolderOpen, Ban, ChevronDown } from 'lucide-react'
import { get, post, put, patch, del, impersonate as apiImpersonate } from '@/lib/api'
import { formatBytes, formatDate } from '@/lib/utils'
import { useAccountUsername } from '@/hooks/useAccount'
import { PHP_VERSIONS } from '@/config/constants'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import { StyledSelect as Select } from '@/components/ui/StyledSelect'
import { DraftChanges, useDraftSection } from '@/components/ui/DraftChanges'
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator } from '@/components/ui/DropdownMenu'
import { Switch } from '@/components/ui/Toggle'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { Tooltip } from '@/components/ui/Tooltip'
import { toast } from '@/components/ui/Toast'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'
import { waitForControlTask } from '@/lib/control-tasks'

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
  ['domains', 'Domains'],
  ['email', 'Email'],
  ['databases', 'Databases'],
  ['backups', 'Backups'],
  ['security', 'Security'],
  ['settings', 'Settings'],
]

const LEGACY_TAB_PARENT = {
  identity: 'settings', advanced: 'settings', apps: 'settings', 'php-functions': 'settings', notes: 'settings', 'usage-limits': 'settings',
  ssl: 'security', processes: 'security',
}

function AdminActions({ username, account, danger = false }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState(null) // 'terminate' | null
  const [terminateProgress, setTerminateProgress] = useState(null)
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['account', username] })
    qc.invalidateQueries({ queryKey: ['accounts'] })
  }
  const suspendMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${username}/suspend`),
    onSuccess: () => { toast.success('Account suspended'); invalidate() },
    onError: (e) => toast.error('Could not suspend', e.message),
  })
  const unsuspendMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${username}/unsuspend`),
    onSuccess: () => { toast.success('Account unsuspended'); invalidate() },
    onError: (e) => toast.error('Could not unsuspend', e.message),
  })
  const terminateMut = useMutation({
    mutationFn: async () => {
      setTerminateProgress(null)
      const task = await post(`/api/v1/task-operations/accounts/${username}/terminate`, {})
      return waitForControlTask(task, { onProgress: setTerminateProgress })
    },
    onSuccess: () => { toast.success('Account terminated'); invalidate(); setConfirm(null); navigate('/accounts', { replace: true }) },
  })
  // Phase 8 feature 1: "Login as user" -> mint+redeem an impersonation token,
  // then hard-navigate to the customer view as that account.
  const impersonateMut = useMutation({
    mutationFn: () => apiImpersonate(username),
    onSuccess: () => { window.location.assign('/app') },
    onError: (e) => toast.error('Could not log in as user', e.message),
  })

  return (
    <div className="account-actions">
      {!danger && <>
        <Button loading={impersonateMut.isPending} disabled={account.status !== 'active'} title={account.status === 'active' ? undefined : 'Unsuspend this account before logging in as the user.'} onClick={() => impersonateMut.mutate()}>
          <UserCog className="h-4 w-4" /> Login as user
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild><Button variant="secondary">More <ChevronDown className="h-4 w-4" /></Button></DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem disabled={account.status !== 'active'} onSelect={() => window.open(`/api/v1/accounts/${username}/files/launch`, '_blank', 'noopener')}><FolderOpen className="h-4 w-4" /> File Manager</DropdownMenuItem>
            {account.status === 'active' && <DropdownMenuItem onSelect={() => setConfirm('suspend')}><Pause className="h-4 w-4" /> Suspend</DropdownMenuItem>}
            {account.status === 'suspended' && <DropdownMenuItem onSelect={() => setConfirm('unsuspend')}><Play className="h-4 w-4" /> Unsuspend</DropdownMenuItem>}
            <DropdownMenuSeparator />
            {['active', 'suspended', 'error'].includes(account.status) && <DropdownMenuItem destructive onSelect={() => setConfirm('terminate')}><Trash2 className="h-4 w-4" /> Terminate</DropdownMenuItem>}
          </DropdownMenuContent>
        </DropdownMenu>
      </>}
      {danger && account.status === 'active' && <Button variant="secondary" loading={suspendMut.isPending} onClick={() => setConfirm('suspend')}><Pause className="h-4 w-4" /> Suspend</Button>}
      {danger && account.status === 'suspended' && <Button variant="secondary" loading={unsuspendMut.isPending} onClick={() => setConfirm('unsuspend')}><Play className="h-4 w-4" /> Unsuspend</Button>}
      {danger && ['active', 'suspended', 'error'].includes(account.status) && <Button variant="danger" onClick={() => setConfirm('terminate')}><Trash2 className="h-4 w-4" /> Terminate</Button>}
      <ConfirmDialog open={confirm === 'suspend' || confirm === 'unsuspend'} onOpenChange={open => !open && setConfirm(null)} title={`${confirm === 'suspend' ? 'Suspend' : 'Unsuspend'} ${username}?`} description={confirm === 'suspend' ? 'Hosting and customer access will be paused. Account files are retained.' : 'Hosting and customer access will resume.'} confirmationText={username} confirmLabel={confirm === 'suspend' ? 'Suspend account' : 'Unsuspend account'} loading={suspendMut.isPending || unsuspendMut.isPending} onConfirm={() => { (confirm === 'suspend' ? suspendMut : unsuspendMut).mutate(undefined, { onSuccess: () => setConfirm(null) }) }} />
      <ConfirmDialog
        open={confirm === 'terminate'}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={`Terminate ${username}?`}
        description={<>This permanently removes the Linux user, vhost, databases, mail and DNS zones. This cannot be undone. Cancel and <Link to={`/accounts/${username}?tab=backups`} className="font-medium text-accent hover:underline">review available backups</Link> first if you need a recovery point.</>}
        confirmLabel="Terminate account"
        confirmationText={username}
        loading={terminateMut.isPending}
        loadingDescription={terminateProgress?.progress_message || `Terminating ${username}… this can take up to 30 seconds. Do not close this page.`}
        error={terminateMut.error}
        onConfirm={() => terminateMut.mutate()}
      />
    </div>
  )
}

function SettingsSection({ title, description, danger = false, children }) {
  return <section className={danger ? 'account-settings-section account-danger-section' : 'account-settings-section'}><div className="account-settings-section-heading"><h2>{title}</h2><p>{description}</p></div>{children}</section>
}

function DisabledReasonButton({ reason, children, ...props }) {
  const button = <Button {...props}>{children}</Button>
  return reason ? <Tooltip content={reason}><span className="inline-flex">{button}</span></Tooltip> : button
}

function resourcePayload(limits) {
  return { cpu_pct: Math.round(Number(limits.cpu_cores) * 100), mem_mb: Math.round(Number(limits.memory_gb) * 1024), io_mb: Number(limits.io_mb), pids_max: Number(limits.pids_max) }
}

function PhpAndLimits({ username, account, part = 'both' }) {
  const qc = useQueryClient()
  const [phpDraft, setPhpVersion] = useState(null)
  const phpVersion = phpDraft ?? account.php_version
  const [touchedLimits,setTouchedLimits] = useState({})
  const originalLimits = {
    cpu_cores: account.cpu_cores ?? account.cpu_pct / 100, memory_gb: account.mem_mb / 1024, io_mb: account.io_mb, pids_max: account.pids_max,
  }
  const [limitsDraft, setLimitsDraft] = useState(null)
  const limits = limitsDraft ?? originalLimits
  const setLimits = update => setLimitsDraft(previous => typeof update === 'function' ? update(previous ?? originalLimits) : update)
  const invalidate = () => qc.invalidateQueries({ queryKey: ['account', username] })

  const phpMut = useMutation({
    mutationFn: version => patch(`/api/v1/accounts/${username}/php-version`, { php_version: version }),
    onSuccess: (_, version) => { toast.success('PHP version updated'); qc.setQueryData(['account', username], previous => ({ ...previous, php_version: version })); setPhpVersion(current => current === version ? null : current); invalidate() },
    onError: (e) => toast.error('Could not change PHP version', e.message),
  })
  const limitsMut = useMutation({
    mutationFn: body => patch(`/api/v1/accounts/${username}/limits`, body),
    onSuccess: (_, body) => { toast.success('Limits updated'); qc.setQueryData(['account', username], previous => ({ ...previous, ...body, cpu_cores: body.cpu_pct / 100 })); setLimitsDraft(current => current && JSON.stringify(resourcePayload(current)) !== JSON.stringify(body) ? current : null); setTouchedLimits({}); invalidate() },
    onError: (e) => toast.error('Could not update resource limits', e.message),
  })

  const limitsDirty = Object.keys(originalLimits).some(key=>Number(limits[key])!==Number(originalLimits[key]))
  const phpDirty = phpVersion !== account.php_version
  const limitsValid = Number.isInteger(Number(limits.pids_max)) && Number(limits.cpu_cores)>=0.01 && Number(limits.cpu_cores)<=(account.host_cpu_cores??Infinity) && Number(limits.memory_gb)>=0.0625 && Number(limits.memory_gb)<=64 && Number(limits.io_mb)>=1 && Number(limits.pids_max)>=10
  const saveLimits = () => limitsMut.mutateAsync(resourcePayload(limits))
  useDraftSection(`account-php-${part}`, { dirty: part !== 'limits' && phpDirty, busy: phpMut.isPending, label: 'PHP version', onSave: () => phpMut.mutateAsync(phpVersion), onDiscard: () => setPhpVersion(null) })
  useDraftSection(`account-resources-${part}`, { dirty: part !== 'php' && limitsDirty, busy: limitsMut.isPending, disabled: !limitsValid, label: 'Resource limits', onSave: saveLimits, onDiscard: () => { setLimitsDraft(null); setTouchedLimits({}) } })

  return (
    <>
      {part !== 'limits' && <SettingsSection title="PHP version" description="Choose the PHP runtime used by this account's websites.">
        <div className="account-setting-fields">
          <FormField label="Version">
            <Select value={phpVersion} onChange={(e) => setPhpVersion(e.target.value)}>
              {PHP_VERSIONS.map((v) => <option key={v} value={v}>PHP {v}</option>)}
            </Select>
          </FormField>
          <DisabledReasonButton reason={!phpDirty ? 'Choose a different PHP version to enable Save.' : null} loading={phpMut.isPending} disabled={!phpDirty} onClick={() => phpMut.mutate(phpVersion)}>Save</DisabledReasonButton>
          {phpMut.isPending&&<p role="status" className="setting-row-description">Changing PHP version…</p>}
        </div>
      </SettingsSection>}

      {part !== 'php' && <SettingsSection title="Resource limits" description="Limit compute resources for this account without changing its files or websites.">
        <div className="account-setting-fields">
          <FormField label="CPU cores" error={touchedLimits.cpu_cores&&!(Number(limits.cpu_cores)>=0.01&&Number(limits.cpu_cores)<=(account.host_cpu_cores??Infinity))?`Enter a core count from 0.01 to ${account.host_cpu_cores??'the server maximum'}.`:null} hint={<>{`Maximum ${account.host_cpu_cores ?? 'server'} cores available.`}{account.configured_cpu_cores > account.host_cpu_cores && <span className="account-field-warning"><AlertTriangle aria-hidden="true" />Stored as {account.configured_cpu_cores} cores; effective use is capped at {account.host_cpu_cores}. Save to reconcile it.</span>}</>}><Input aria-label="CPU cores" type="number" min="0.01" max={account.host_cpu_cores} step="0.01" value={limits.cpu_cores} onBlur={()=>setTouchedLimits(t=>({...t,cpu_cores:true}))} onChange={e=>setLimits(l=>({...l,cpu_cores:e.target.value}))}/></FormField>
          <FormField label="Memory (GB)" error={touchedLimits.memory_gb&&!(Number(limits.memory_gb)>=0.0625&&Number(limits.memory_gb)<=64)?'Memory must be between 0.0625 and 64 GB.':null} hint={`Server RAM: ${account.host_memory_gb?.toFixed(1) ?? 'n/a'} GB. Allocations can overcommit shared RAM.`}><Input aria-label="Memory (GB)" type="number" min="0.0625" step={1 / 1024} value={limits.memory_gb} onBlur={()=>setTouchedLimits(t=>({...t,memory_gb:true}))} onChange={e=>setLimits(l=>({...l,memory_gb:e.target.value}))}/></FormField>
          <FormField label="Disk IO (MB/s)" error={touchedLimits.io_mb&&!(Number(limits.io_mb)>=1)?'Enter an IO limit of at least 1 MB/s.':null}><Input aria-label="Disk IO (MB/s)" type="number" min="1" value={limits.io_mb} onBlur={()=>setTouchedLimits(t=>({...t,io_mb:true}))} onChange={e=>setLimits(l=>({...l,io_mb:e.target.value}))}/></FormField>
          <FormField label="Max processes" error={touchedLimits.pids_max&&!(Number.isInteger(Number(limits.pids_max))&&Number(limits.pids_max)>=10)?'Enter a whole process count of at least 10.':null}><Input aria-label="Max processes" type="number" min="10" value={limits.pids_max} onBlur={()=>setTouchedLimits(t=>({...t,pids_max:true}))} onChange={e=>setLimits(l=>({...l,pids_max:e.target.value}))}/></FormField>
          {Number(limits.memory_gb) > account.host_memory_gb && <p className="text-sm text-warning">This allocation exceeds total server RAM. Simultaneous usage by accounts can exhaust memory.</p>}
          <DisabledReasonButton reason={!limitsValid ? 'Correct the invalid values before updating limits.' : !limitsDirty ? 'Change a resource value to enable Update limits.' : null} loading={limitsMut.isPending} disabled={!limitsValid || !limitsDirty} onClick={() => saveLimits().catch(() => {})}>Update limits</DisabledReasonButton>
          {limitsMut.isPending&&<p role="status" className="setting-row-description">Applying resource limits…</p>}
        </div>
      </SettingsSection>}
    </>
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

  return (
    <SettingsSection title="Plan" description={`Current plan: ${currentPlan ? currentPlan.name : 'Custom plan'}. Applying a plan updates this account's configured limits.`}>
      <div className="account-setting-fields">
        <FormField label="Plan">
          <Select value={planId} onChange={(e) => setPlanId(e.target.value)}>
            <option value="">Select a plan…</option>
            {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </FormField>
        <DisabledReasonButton reason={!planId ? 'Choose a plan to enable Apply.' : null} loading={applyMut.isPending} disabled={!planId} onClick={() => applyMut.mutate(planId)}>Apply</DisabledReasonButton>
        {applyMut.isPending&&<p role="status" className="setting-row-description">Applying plan settings…</p>}
      </div>
    </SettingsSection>
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
  return <SettingsSection title="Isolation" description="Keep web and PHP file visibility inside this account's mount namespace."><div className="account-isolation-control"><div><strong>Namespace isolation</strong><span>{data ? (data.enabled ? 'Active' : data.eligible ? 'Not active' : 'Not eligible') : 'n/a'}</span></div><Switch aria-label="Namespace isolation" checked={!!data?.enabled} onCheckedChange={(v) => mut.mutate(v)} disabled={!data || !data.eligible || mut.isPending} /></div>{mut.isPending&&<span role="status" className="setting-row-description">Updating account isolation…</span>}</SettingsSection>
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
  const [draft, setDraft] = useState(null)
  const form = draft ?? originalForm
  const setForm = update => setDraft(previous => typeof update === 'function' ? update(previous ?? originalForm) : update)

  const mut = useMutation({
    mutationFn: (body) => patch(`/api/v1/accounts/${username}/usage-limits`, body),
    onSuccess: (_, body) => { toast.success('Usage limits updated'); qc.setQueryData(['usage-limits', username], previous => ({ ...previous, ...body })); qc.invalidateQueries({ queryKey: ['usage-limits', username] }) },
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
    const submitted = { ...form }
    return mut.mutateAsync(body).then(result => {
      setDraft(current => current && JSON.stringify(current) !== JSON.stringify(submitted) ? current : null)
      return result
    })
  }
  const valid=USAGE_LIMIT_FIELDS.every(f=>form[f.key]===''||(Number.isFinite(Number(form[f.key]))&&Number(form[f.key])>=1&&(f.apiKey||Number.isInteger(Number(form[f.key])))))
  const dirty = USAGE_LIMIT_FIELDS.some(field => {
    const current = form[field.key] === '' ? null : Number(form[field.key])
    const apiValue = field.apiKey && current != null ? Math.round(current * 1024) : current
    return apiValue !== (data[field.apiKey || field.key] ?? null)
  }) || form.auto_suspend_at_100 !== originalForm.auto_suspend_at_100
  useDraftSection('account-usage-limits', { dirty, busy: mut.isPending, disabled: !valid, label: 'Advanced account limits', onSave: save, onDiscard: () => setDraft(null) })
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
          aria-label="Auto-suspend at 100%"
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

  return <details className="account-usage-limits"><summary>Additional usage limits</summary><p className="account-usage-limits-intro">Bandwidth, database, mailbox and subdomain limits.</p><div className="account-usage-limits-content">
        {isLoading ? (
          <CenteredSpinner label="Loading usage limits…" />
        ) : error ? (
          <ErrorState error={error} onRetry={refetch} />
        ) : data ? (
          <UsageLimitsForm key={username} username={username} data={data} />
        ) : null}
      </div></details>
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
  const {data:plans}=useQuery({queryKey:['plans'],queryFn:()=>get('/api/v1/admin/plans'),retry:false})
  const plan=(plans?.plans||[]).find(row=>row.id===account.plan_id)
  const summary = `${plan?.name||account.plan_name||'Custom plan'} · ${account.primary_domain||'No primary domain'} · UID ${account.uid??'n/a'} · PHP ${account.php_version||'n/a'}`
  return <section className="account-summary" aria-label="Account summary">
    <PageHeader title={username} titleMeta={<StatusBadge status={account.status}/>} description={summary}><AdminActions username={username} account={account}/></PageHeader>
  </section>
}

function UsageRow({ label, value, percent }) {
  const safePercent = percent == null ? null : Math.min(100, Math.max(0, percent))
  return <div className="account-usage-row"><span className="account-usage-label">{label}</span><strong>{value}</strong>{safePercent != null && <div className="account-usage-track" role="progressbar" aria-label={`${label} usage`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(safePercent)}><span style={{width:`${safePercent}%`}} /></div>}</div>
}

function AccountOverview({ username, account }) {
  const {data:usage}=useQuery({queryKey:['usage',username],queryFn:()=>get(`/api/v1/accounts/${username}/usage`),refetchInterval:15000,retry:false})
  const {data:plans}=useQuery({queryKey:['plans'],queryFn:()=>get('/api/v1/admin/plans'),retry:false})
  const {data:namespace}=useQuery({queryKey:['namespace',username],queryFn:()=>get(`/api/v1/accounts/${username}/namespace`),retry:false})
  const counters=usage?.resources
  const disk=usage?.current?.disk_total_bytes
  const diskLimit=account.quota_hard_mb ? account.quota_hard_mb*1048576 : null
  const bandwidth=usage?.bandwidth_month_to_date_bytes
  const bandwidthLimit=counters?.bandwidth_limit_bytes
  const memory=counters?.memory_current_bytes
  const memoryLimit=counters?.memory_limit_bytes
  const inodes=usage?.current?.inode_count
  const inodeLimit=account.inode_limit??usage?.inode_limit??null
  const plan=(plans?.plans||[]).find(row=>row.id===account.plan_id)
  const rows=[
    {label:'Disk',value:disk==null?'n/a':`${formatBytes(disk)} of ${diskLimit?formatBytes(diskLimit):'unlimited'}`,percent:diskLimit&&disk!=null?disk/diskLimit*100:null},
    {label:'Bandwidth',value:bandwidth==null?'n/a':`${formatBytes(bandwidth)} of ${bandwidthLimit?formatBytes(bandwidthLimit):'unlimited'}`,percent:bandwidthLimit&&bandwidth!=null?bandwidth/bandwidthLimit*100:null},
    {label:'Memory',value:memory==null?'n/a':`${formatBytes(memory)} of ${memoryLimit?formatBytes(memoryLimit):'unlimited'}`,percent:memoryLimit&&memory!=null?memory/memoryLimit*100:null},
    {label:'CPU',value:'n/a',percent:null},
    {label:'Inodes',value:inodes==null?'n/a':`${inodes.toLocaleString()} of ${inodeLimit?Number(inodeLimit).toLocaleString():'unlimited'}`,percent:inodeLimit&&inodes!=null?inodes/inodeLimit*100:null},
  ]
  const facts=[
    ['Plan',plan?.name||account.plan_name||'Custom plan'],
    ['Primary domain',account.primary_domain||'n/a'],
    ['PHP version',account.php_version?`PHP ${account.php_version}`:'n/a'],
    ['Created',account.created_at?formatDate(account.created_at):'n/a'],
    ['Namespace isolation',namespace?namespace.enabled?'Active':'Not active':'n/a'],
  ]
  return <div className="account-overview">
    <section className="account-overview-card"><h2>Usage</h2><div className="account-usage-list">{rows.map(row=><UsageRow key={row.label} {...row}/>)}</div></section>
    <section className="account-overview-card"><h2>Account</h2><dl className="account-fact-list">{facts.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></section>
  </div>
}

function AccountSettings({ username, account, setSearchParams }) {
  return <div className="account-settings">
    <PhpAndLimits username={username} account={account} part="php"/>
    <PlanCard username={username} account={account}/>
    <PhpAndLimits username={username} account={account} part="limits"/>
    <NamespaceCard username={username}/>
    <SettingsSection title="Applications and advanced settings" description="Open a focused tool when you need to change application or account-level configuration.">
      <div className="account-settings-links">
        {[['apps','Applications'],['identity','Account identity'],['usage-limits','Advanced account limits'],['php-functions','PHP functions'],['notes','Notes']].map(([tab,label])=><Button variant="link" key={tab} onClick={()=>setSearchParams({tab})}>{label}</Button>)}
      </div>
    </SettingsSection>
    <SettingsSection danger title="Danger zone" description={`Pause hosting or permanently remove ${username}. Review available backups before termination.`}><AdminActions username={username} account={account} danger/></SettingsSection>
  </div>
}

function SecurityLanding({ setSearchParams }) {
  return <div className="account-tool-choices"><h2>Security tools</h2><p>Open one security task at a time.</p><div>{[['ssl','SSL certificates'],['php-functions','PHP functions'],['processes','Processes']].map(([tab,label])=><Button variant="secondary" key={tab} onClick={()=>setSearchParams({tab})}>{label}</Button>)}</div></div>
}

function AccountDetailContent() {
  const navigate = useNavigate()
  const { username } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedTab = searchParams.get('tab')
  const activeTab = ACCOUNT_TABS.some(([value])=>value===requestedTab) ? requestedTab : LEGACY_TAB_PARENT[requestedTab] || 'overview'
  const { data: account, isLoading, error, refetch } = useQuery({
    queryKey: ['account', username],
    queryFn: () => get(`/api/v1/accounts/${username}`),
    enabled: !!username,
  })

  useEffect(() => { if (account?.status === 'terminated') { toast.info('This account has been terminated'); navigate('/accounts', { replace: true }) } }, [account?.status, navigate])

  return (
    <div className="reference-page">
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
              {ACCOUNT_TABS.map(([value,label])=><TabsTrigger key={value} value={value}>{label}</TabsTrigger>)}
            </TabsList>

            <TabsContent value="overview"><AccountOverview username={username} account={account}/></TabsContent>
            <TabsContent value="domains" className="account-detail-tab"><Domains /></TabsContent>
            <TabsContent value="databases" className="account-detail-tab"><Databases /></TabsContent>
            <TabsContent value="email" className="account-detail-tab"><Email /></TabsContent>
            <TabsContent value="backups" className="account-detail-tab"><Backups /></TabsContent>
            <TabsContent value="security" className="account-detail-tab">{requestedTab==='ssl'?<><h2 className="sr-only">SSL/TLS</h2><Ssl/></>:requestedTab==='processes'?<Processes embedded/>:<SecurityLanding setSearchParams={setSearchParams}/>}</TabsContent>
            <TabsContent value="settings" className="account-detail-tab">{requestedTab==='apps'?<Apps/>:requestedTab==='identity'||requestedTab==='advanced'?<AccountIdentity username={username} account={account}/>:requestedTab==='usage-limits'?<UsageLimitsCard/>:requestedTab==='php-functions'?<PhpFunctionsTab username={username}/>:requestedTab==='notes'?<AccountNotes username={username}/>:<AccountSettings username={username} account={account} setSearchParams={setSearchParams}/>}</TabsContent>
          </Tabs>
        </>
      ) : null}
    </div>
  )
}

export default function AccountDetail(){return <DraftChanges><AccountDetailContent/></DraftChanges>}
