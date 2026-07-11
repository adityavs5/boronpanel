import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Users, ShieldCheck, Play, ArrowUpCircle } from 'lucide-react'
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
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ username: '', primary_domain: '', plan_id: '', email: '', password: '' })
  const [selected, setSelected] = useState(() => new Set())
  // Panel update system: version in the dashboard header + update banner.
  const version = useVersion()
  const { data: updateStatus } = useUpdateStatus()

  // Run A feature 1: optional plan applied atomically right after creation.
  const { data: plansData } = useQuery({
    queryKey: ['plans'],
    queryFn: () => get('/api/v1/admin/plans'),
  })
  const plans = plansData?.plans || []

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
      toast.success('Account created', `${acc.username} is being provisioned.`)
      qc.invalidateQueries({ queryKey: ['accounts'] })
      setOpen(false)
      setForm({ username: '', primary_domain: '', plan_id: '', email: '', password: '' })
      navigate(`/accounts/${acc.username}`)
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
        searchPlaceholder="Search accounts…"
        pageSize={15}
        getRowKey={(r) => r.username}
        onRowClick={(r) => navigate(`/accounts/${r.username}`)}
        emptyTitle="No accounts yet"
        emptyDescription="Create your first hosting account to get started."
        emptyIcon={Users}
        emptyAction={<Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> Create account</Button>}
      />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Create hosting account</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({
                username: form.username,
                primary_domain: form.primary_domain || undefined,
                plan_id: form.plan_id ? Number(form.plan_id) : undefined,
                email: form.email.trim() || undefined,
                password: form.password || undefined,
              })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Username" required hint="Lowercase letters and digits, starts with a letter (max 16 chars).">
                <Input
                  autoFocus
                  value={form.username}
                  onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
                  pattern="[a-z][a-z0-9]{0,15}"
                  placeholder="acme1"
                  required
                />
              </FormField>
              <FormField label="Primary domain" hint="Optional — can be added later.">
                <Input
                  value={form.primary_domain}
                  onChange={(e) => setForm((f) => ({ ...f, primary_domain: e.target.value }))}
                  placeholder="example.com"
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
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending}>Create account</Button>
            </DialogFooter>
          </form>
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
