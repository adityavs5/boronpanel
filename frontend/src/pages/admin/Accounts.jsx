import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Copy, Plus, Users, ShieldCheck, Play, ArrowUpCircle } from 'lucide-react'
import { useUpdateStatus } from '@/hooks/useUpdateStatus'
import { useVersion } from '@/hooks/useVersion'
import { get, post } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, Textarea, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Checkbox } from '@/components/ui/Toggle'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

// Phase 8 feature 12: bulk action bar (suspend/unsuspend/update-limits/notify),
// async with per-account progress, stops on first failure.
function BulkActionBar({ selected, clearSelection }) {
  const qc = useQueryClient()
  const [action, setAction] = useState('suspend')
  const [limits, setLimits] = useState({ cpu_pct: '', mem_mb: '', io_mb: '', pids_max: '' })
  const [notify, setNotify] = useState({ subject: '', body: '' })
  const [jobId, setJobId] = useState(null)

  const triggerMut = useMutation({
    mutationFn: () => {
      const action_params = {}
      if (action === 'update_limits') for (const k of ['cpu_pct', 'mem_mb', 'io_mb', 'pids_max']) if (limits[k] !== '') action_params[k] = Number(limits[k])
      if (action === 'notify') { action_params.subject = notify.subject.trim(); action_params.body = notify.body.trim() }
      return post('/api/v1/admin/accounts/bulk-action', { action, usernames: [...selected], action_params })
    },
    onSuccess: (job) => { setJobId(job.id); toast.success('Bulk action started', `${selected.size} accounts`) },
    onError: (e) => toast.error('Could not start bulk action', e.message),
  })

  const { data: job } = useQuery({
    queryKey: ['bulk-action', jobId],
    queryFn: () => get(`/api/v1/admin/accounts/bulk-action/${jobId}`),
    enabled: jobId != null,
    refetchInterval: (q) => { const s = q.state.data?.status; return s === 'pending' || s === 'running' ? 1500 : false },
  })
  const finished = job && (job.status === 'completed' || job.status === 'failed')
  if (finished && jobId) { qc.invalidateQueries({ queryKey: ['accounts'] }) }

  return (
    <div className="mb-4 rounded-card border border-accent/40 bg-accent/5 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <span className="text-sm font-medium text-foreground">{selected.size} selected</span>
        <FormField label="Action" className="w-48">
          <Select value={action} onChange={(e) => { setAction(e.target.value); setJobId(null) }}>
            <option value="suspend">Suspend</option>
            <option value="unsuspend">Unsuspend</option>
            <option value="update_limits">Update limits</option>
            <option value="notify">Send notification</option>
          </Select>
        </FormField>
        <Button loading={triggerMut.isPending} onClick={() => triggerMut.mutate()}><Play className="h-4 w-4" /> Apply</Button>
        <Button variant="ghost" onClick={clearSelection}>Clear</Button>
      </div>

      {action === 'update_limits' && (
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {['cpu_pct', 'mem_mb', 'io_mb', 'pids_max'].map((k) => (
            <FormField key={k} label={k}>
              <Input type="number" placeholder="unchanged" value={limits[k]} onChange={(e) => setLimits((l) => ({ ...l, [k]: e.target.value }))} />
            </FormField>
          ))}
        </div>
      )}
      {action === 'notify' && (
        <div className="mt-3 space-y-2">
          <Input placeholder="Subject" value={notify.subject} onChange={(e) => setNotify((n) => ({ ...n, subject: e.target.value }))} />
          <Textarea rows={2} placeholder="Message body" value={notify.body} onChange={(e) => setNotify((n) => ({ ...n, body: e.target.value }))} />
        </div>
      )}

      {job && (
        <div className="mt-3 text-sm">
          <StatusBadge status={job.status} /> <span className="text-muted-foreground">{job.completed_count} / {job.total}{job.current_username ? ` · ${job.current_username}` : ''}</span>
          {job.error && <p className="mt-1 text-danger">{job.error}</p>}
          {(job.results || []).length > 0 && (
            <ul className="mt-2 max-h-40 space-y-0.5 overflow-auto text-xs">
              {job.results.map((r) => (
                <li key={r.username} className={r.ok ? 'text-success' : 'text-danger'}>{r.ok ? '✓' : '✗'} {r.username} — {r.detail}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

export default function Accounts() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [open, setOpen] = useState(false)
  const [createStep, setCreateStep] = useState(1)
  const [createdAccount, setCreatedAccount] = useState(null)
  const [form, setForm] = useState({ username: '', primary_domain: '', plan_id: '', email: '', password: '', ip_selection: 'automatic', server_ip_id: '' })
  const [selected, setSelected] = useState(() => new Set())
  const listQuery = searchParams.get('q') || ''
  const listPage = Math.max(1, Number.parseInt(searchParams.get('page') || '1', 10) || 1)
  const sortKey = searchParams.get('sort')
  const listSort = sortKey ? { key: sortKey, dir: searchParams.get('dir') === 'desc' ? 'desc' : 'asc' } : null
  const usernameError = form.username && !/^[a-z][a-z0-9]{0,15}$/.test(form.username)
    ? 'Use 1–16 lowercase letters or digits, starting with a letter.'
    : undefined
  const domainError = form.primary_domain && !/^(?=.{1,253}$)(?!-)[a-z0-9-]+(?:\.[a-z0-9-]+)+$/i.test(form.primary_domain)
    ? 'Enter a valid domain such as example.com.'
    : undefined
  const passwordError = form.password && !/^(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])(?=.*[^A-Za-z0-9]).{12,}$/.test(form.password)
    ? 'Use 12+ characters with upper, lower, number, and symbol.'
    : undefined

  function setListParam(key, value, defaultValue = '') {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous)
      if (value === defaultValue || value == null) next.delete(key)
      else next.set(key, String(value))
      return next
    })
  }
  // Panel update system: version in the dashboard header + update banner.
  const version = useVersion()
  const { data: updateStatus } = useUpdateStatus()

  // Run A feature 1: optional plan applied atomically right after creation.
  const { data: plansData } = useQuery({
    queryKey: ['plans'],
    queryFn: () => get('/api/v1/admin/plans'),
  })
  const plans = plansData?.plans || []
  const { data: ipData } = useQuery({
    queryKey: ['ip-management'],
    queryFn: () => get('/api/v1/admin/ip-management'),
  })
  const availableIps = (ipData?.ips || []).filter((entry) => entry.active && entry.present_on_host)

  const toggle = (username) => setSelected((prev) => {
    const next = new Set(prev)
    next.has(username) ? next.delete(username) : next.add(username)
    return next
  })
  const clearSelection = () => setSelected(new Set())

  // Namespace bulk-enable (admin migration). Trigger returns a job we then
  // poll every 2s while it runs; progress is surfaced in the ConfirmDialog.
  const [nsOpen, setNsOpen] = useState(false)
  const [jobId, setJobId] = useState(null)
  const notifiedRef = useRef(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => get('/api/v1/accounts'),
  })

  const createMut = useMutation({
    mutationFn: (body) => post('/api/v1/accounts', body),
    onSuccess: (acc) => {
      if (acc.plan_apply_error) toast.warning('Account created, but the plan needs attention', acc.plan_apply_error)
      else toast.success('Account created', 'Save the one-time credentials before continuing.')
      qc.invalidateQueries({ queryKey: ['accounts'] })
      setOpen(false)
      setCreateStep(1)
      setForm({ username: '', primary_domain: '', plan_id: '', email: '', password: '', ip_selection: 'automatic', server_ip_id: '' })
      setCreatedAccount(acc)
    },
    onError: (e) => toast.error('Could not create account', e.message),
  })

  const bulkMut = useMutation({
    mutationFn: () => post('/api/v1/accounts/namespace/bulk-enable'),
    onSuccess: (job) => { notifiedRef.current = null; setJobId(job.id) },
    onError: (e) => toast.error('Could not start bulk-enable', e.message),
  })

  const { data: job } = useQuery({
    queryKey: ['ns-bulk-enable', jobId],
    queryFn: () => get(`/api/v1/accounts/namespace/bulk-enable/${jobId}`),
    enabled: jobId != null,
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'pending' || s === 'running' ? 2000 : false
    },
  })

  const running = bulkMut.isPending || (!!job && (job.status === 'pending' || job.status === 'running'))
  const finished = !!job && (job.status === 'completed' || job.status === 'failed')

  // Fire a single toast (and refresh accounts) when a job reaches a terminal
  // state, so completion is visible even if the dialog is closed mid-run.
  useEffect(() => {
    if (!finished || notifiedRef.current === job.id) return
    notifiedRef.current = job.id
    if (job.status === 'completed') {
      toast.success('Namespace isolation enabled', `${job.completed_count} / ${job.total} accounts enabled.`)
    } else {
      toast.error('Bulk-enable stopped', job.error || `Stopped after ${job.completed_count} / ${job.total} accounts.`)
    }
    qc.invalidateQueries({ queryKey: ['accounts'] })
  }, [finished, job, qc])

  function resetBulk() {
    setJobId(null)
    notifiedRef.current = null
    bulkMut.reset()
  }

  function handleNsConfirm() {
    if (finished) { setNsOpen(false); resetBulk(); return }
    if (!jobId) bulkMut.mutate()
  }

  let nsDescription
  if (finished && job.status === 'completed') {
    nsDescription = <>Done — <strong className="text-foreground">{job.completed_count} / {job.total}</strong> accounts enabled.</>
  } else if (finished) {
    nsDescription = (
      <>Stopped after <strong className="text-foreground">{job.completed_count} / {job.total}</strong> accounts.<br />{job.error}</>
    )
  } else if (running) {
    nsDescription = (
      <>
        Enabling isolation… <strong className="text-foreground">{job?.completed_count ?? 0} / {job?.total ?? '…'}</strong> accounts done.
        {job?.current_username ? <><br />Currently: {job.current_username}</> : null}
      </>
    )
  } else {
    nsDescription = 'Enables mount-namespace isolation for every currently active account that is not already enabled, one at a time. It stops at the first account that fails verification. This runs in the background and may take a while.'
  }

  // Terminated accounts are gone for good — the API already excludes them,
  // this is a belt-and-braces filter. Their record lives in the Account Log.
  const accounts = (data || []).filter((a) => a.status !== 'terminated')
  const allOnPage = accounts.map((r) => r.username)
  const allSelected = allOnPage.length > 0 && allOnPage.every((u) => selected.has(u))
  const columns = [
    {
      key: 'select', searchable: false, sortable: false,
      headerClassName: 'w-10',
      header: (
        <Checkbox
          checked={allSelected}
          onCheckedChange={() => setSelected(allSelected ? new Set() : new Set(allOnPage))}
          aria-label="Select all accounts"
        />
      ),
      render: (r) => (
        <span onClick={(e) => e.stopPropagation()} className="flex items-center">
          <Checkbox
            checked={selected.has(r.username)}
            onCheckedChange={() => toggle(r.username)}
            aria-label={`Select ${r.username}`}
          />
        </span>
      ),
    },
    { key: 'username', header: 'Username', sortable: true, searchable: true, render: (r) => <span className="font-medium text-foreground">{r.username}</span> },
    { key: 'status', header: 'Status', sortable: true, render: (r) => <StatusBadge status={r.status} /> },
    { key: 'primary_domain', header: 'Primary domain', searchable: true, render: (r) => r.primary_domain || <span className="text-muted-foreground">—</span> },
    { key: 'server_ip', header: 'Server IP', searchable: true, render: (r) => <span className="font-mono text-xs">{r.server_ip || '—'}</span> },
    { key: 'created_at', header: 'Created', sortable: true, render: (r) => (r.created_at ? formatDate(r.created_at) : '—') },
  ]

  return (
    <div>
      <PageHeader
        title="Accounts"
        description={`Manage all hosting accounts on this server. Boron ${version}.`}
        icon={Users}
      >
        <Button variant="secondary" onClick={() => setNsOpen(true)}>
          <ShieldCheck className="h-4 w-4" /> Namespace: bulk-enable
        </Button>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Create account
        </Button>
      </PageHeader>

      {updateStatus?.update_available && (
        // Panel update system: dashboard banner. Teal, informational -- the
        // actual apply flow (2FA confirm, progress) lives on /updates.
        <div className="mb-4 flex items-center justify-between gap-3 rounded-card border border-accent/40 bg-accent-50 px-4 py-3 text-sm dark:bg-accent-950/40">
          <div className="flex items-center gap-2.5 text-accent-700 dark:text-accent-300">
            <ArrowUpCircle className="h-5 w-5 shrink-0" />
            <span>
              <span className="font-semibold">Boron v{updateStatus.latest_version} is available</span>
              {' '}(you are on v{updateStatus.current_version}).
            </span>
          </div>
          <Button size="sm" asChild>
            <Link to="/updates">View update</Link>
          </Button>
        </div>
      )}

      {selected.size > 0 && <BulkActionBar selected={selected} clearSelection={clearSelection} />}

      <DataTable
        columns={columns}
        data={accounts}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        query={listQuery}
        onQueryChange={(value) => {
          setSearchParams((previous) => {
            const next = new URLSearchParams(previous)
            value ? next.set('q', value) : next.delete('q')
            next.delete('page')
            return next
          })
        }}
        page={listPage}
        onPageChange={(value) => setListParam('page', value, 1)}
        sort={listSort}
        onSortChange={(value) => {
          setSearchParams((previous) => {
            const next = new URLSearchParams(previous)
            if (value) {
              next.set('sort', value.key)
              next.set('dir', value.dir)
            } else {
              next.delete('sort')
              next.delete('dir')
            }
            next.delete('page')
            return next
          })
        }}
        searchPlaceholder="Search accounts…"
        pageSize={15}
        getRowKey={(r) => r.username}
        onRowClick={(r) => navigate(`/accounts/${r.username}`)}
        emptyTitle="No accounts yet"
        emptyDescription="Create your first hosting account to get started."
        emptyIcon={Users}
        emptyAction={<Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> Create account</Button>}
      />

      <Dialog open={open} onOpenChange={(value) => {
        setOpen(value)
        if (!value && !createMut.isPending) setCreateStep(1)
      }}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Create hosting account · Step {createStep} of 2</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (createStep === 1) {
                setCreateStep(2)
                return
              }
              createMut.mutate({
                username: form.username,
                primary_domain: form.primary_domain || undefined,
                plan_id: form.plan_id ? Number(form.plan_id) : undefined,
                email: form.email.trim() || undefined,
                password: form.password || undefined,
                ip_selection: form.ip_selection,
                server_ip_id: form.ip_selection === 'specific' ? Number(form.server_ip_id) : undefined,
              })
            }}
          >
            {createStep === 1 ? (
            <DialogBody className="space-y-4">
              <FormField label="Username" required hint="Lowercase letters and digits, starts with a letter (max 16 chars)." error={usernameError}>
                <Input
                  autoFocus
                  value={form.username}
                  onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
                  pattern="[a-z][a-z0-9]{0,15}"
                  placeholder="acme1"
                  required
                />
              </FormField>
              <FormField label="Primary domain" hint="Optional — can be added later." error={domainError}>
                <Input
                  value={form.primary_domain}
                  onChange={(e) => setForm((f) => ({ ...f, primary_domain: e.target.value }))}
                  placeholder="example.com"
                  pattern="(?=.{1,253}$)(?!-)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
                />
              </FormField>
              <FormField label="Contact email" hint="Optional — used for the welcome email and account notifications. Can be added/changed later.">
                <Input
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                  placeholder="owner@example.com"
                />
              </FormField>
              <FormField
                label="Password"
                hint="Optional — leave blank to auto-generate a strong password. If set: 12+ characters with upper, lower, a number, and a symbol."
                error={passwordError}
              >
                <Input
                  type="password"
                  autoComplete="new-password"
                  value={form.password}
                  onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                  placeholder="Auto-generated if blank"
                  pattern={form.password ? '(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])(?=.*[^A-Za-z0-9]).{12,}' : undefined}
                  title="At least 12 characters, including an uppercase letter, a lowercase letter, a number, and a symbol."
                />
              </FormField>
              <FormField label="Plan" hint="Optional — applies the plan's limits immediately after creation.">
                <Select value={form.plan_id} onChange={(e) => setForm((f) => ({ ...f, plan_id: e.target.value }))}>
                  <option value="">No plan (default limits)</option>
                  {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </Select>
              </FormField>
              <FormField label="IP assignment" hint="Automatic follows the server-wide policy in IP Management.">
                <Select value={form.ip_selection} onChange={(e) => setForm((f) => ({ ...f, ip_selection: e.target.value }))}>
                  <option value="automatic">Automatic (server policy)</option>
                  <option value="primary">Primary server IP</option>
                  <option value="random">Random shared IP</option>
                  <option value="specific">Choose a specific IP</option>
                </Select>
              </FormField>
              {form.ip_selection === 'specific' && <FormField label="Server IP" required>
                <Select required value={form.server_ip_id} onChange={(e) => setForm((f) => ({ ...f, server_ip_id: e.target.value }))}>
                  <option value="">Choose an IP…</option>
                  {availableIps.map((entry) => <option key={entry.id} value={entry.id}>{entry.address} — {entry.allocation_mode}{entry.label ? ` · ${entry.label}` : ''}</option>)}
                </Select>
              </FormField>}
            </DialogBody>
            ) : (
              <DialogBody className="space-y-4">
                <p className="text-sm text-muted-foreground">Review these settings before provisioning the account. You can return to edit without losing your entries.</p>
                <dl className="divide-y divide-border rounded-card border border-border text-sm">
                  {[
                    ['Username', form.username],
                    ['Primary domain', form.primary_domain || 'Add later'],
                    ['Contact email', form.email.trim() || 'Add later'],
                    ['Password', form.password ? 'Use the password entered' : 'Generate a strong password'],
                    ['Plan', plans.find((plan) => String(plan.id) === String(form.plan_id))?.name || 'Default limits'],
                    ['Server IP', form.ip_selection === 'specific' ? (availableIps.find((entry) => String(entry.id) === String(form.server_ip_id))?.address || 'Choose an IP') : ({ automatic: 'Automatic server policy', primary: 'Primary server IP', random: 'Random shared IP' }[form.ip_selection])],
                  ].map(([label, value]) => (
                    <div key={label} className="grid grid-cols-[7rem_1fr] gap-3 px-3 py-2.5">
                      <dt className="text-muted-foreground">{label}</dt>
                      <dd className="min-w-0 break-words font-medium text-foreground">{value}</dd>
                    </div>
                  ))}
                </dl>
                <div className="rounded-card border border-warning/40 bg-warning/5 p-3 text-sm text-foreground">
                  Creating the account provisions system resources. If applying a plan fails after account creation, the account remains available so it can be repaired safely.
                </div>
              </DialogBody>
            )}
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => {
                if (createStep === 1) {
                  setOpen(false)
                  setCreateStep(1)
                } else setCreateStep(1)
              }} disabled={createMut.isPending}>
                {createStep === 1 ? 'Cancel' : 'Back'}
              </Button>
              <Button type="submit" loading={createMut.isPending}>{createStep === 1 ? 'Review account' : 'Create account'}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={!!createdAccount} onOpenChange={() => {}}>
        <DialogContent size="sm" showClose={false} onEscapeKeyDown={(event) => event.preventDefault()} onPointerDownOutside={(event) => event.preventDefault()}>
          <DialogHeader>
            <DialogTitle>Save the account credentials</DialogTitle>
          </DialogHeader>
          <DialogBody className="space-y-4">
            <p className="text-sm text-muted-foreground">The initial password is shown only now. Copy it into your password manager before continuing.</p>
            {createdAccount?.plan_apply_error && (
              <div className="rounded-card border border-warning/40 bg-warning/10 p-3 text-sm text-foreground" role="alert">
                <div className="font-medium">The account exists, but its plan was not fully applied.</div>
                <div className="mt-1 text-muted-foreground">{createdAccount.plan_apply_error} Save the credentials, then review the account plan and limits.</div>
              </div>
            )}
            {createdAccount?.ip_assignment_error && <div className="rounded-card border border-warning/40 bg-warning/10 p-3 text-sm text-foreground" role="alert"><div className="font-medium">The account exists, but its IP assignment needs attention.</div><div className="mt-1 text-muted-foreground">{createdAccount.ip_assignment_error} Save the credentials, then assign an IP from IP Management.</div></div>}
            <div className="space-y-3 rounded-card border border-border p-3">
              {[
                ['Username', createdAccount?.username],
                ['Initial password', createdAccount?.initial_password],
              ].map(([label, value]) => (
                <div key={label}>
                  <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
                  <div className="flex items-center gap-2">
                    <code className="min-w-0 flex-1 select-all break-all rounded-btn bg-muted px-2.5 py-2 text-sm text-foreground">{value}</code>
                    <Button
                      variant="outline"
                      size="icon"
                      aria-label={`Copy ${label.toLowerCase()}`}
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(value || '')
                          toast.success(`${label} copied`)
                        } catch {
                          toast.error('Copy failed', 'Select the value and copy it manually.')
                        }
                      }}
                    >
                      <Copy className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button onClick={() => {
              const username = createdAccount.username
              setCreatedAccount(null)
              navigate(`/accounts/${username}`)
            }}>I saved the credentials</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={nsOpen}
        onOpenChange={(v) => { setNsOpen(v); if (!v && !running) resetBulk() }}
        title="Enable namespace isolation for all accounts"
        description={nsDescription}
        confirmLabel={finished ? 'Close' : running ? 'Enabling…' : 'Enable for all eligible'}
        variant="primary"
        loading={running}
        onConfirm={handleNsConfirm}
      />
    </div>
  )
}
