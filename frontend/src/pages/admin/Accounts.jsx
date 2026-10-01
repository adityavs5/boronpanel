import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Users, ShieldCheck, Play, ArrowUpCircle, MoreHorizontal } from 'lucide-react'
import { useUpdateStatus } from '@/hooks/useUpdateStatus'
import { useVersion } from '@/hooks/useVersion'
import { get, patch, post } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, Textarea, FormField } from '@/components/ui/Input'
import { StyledSelect as Select } from '@/components/ui/StyledSelect'
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from '@/components/ui/DropdownMenu'
import { Checkbox } from '@/components/ui/Toggle'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

// Phase 8 feature 12: bulk action bar (suspend/unsuspend/update-limits/notify),
// async with per-account progress, stops on first failure.
function BulkActionBar({ selected, clearSelection }) {
  const qc = useQueryClient()
  const [action, setAction] = useState('suspend')
  const [limits, setLimits] = useState({ cpu_cores: '', mem_mb: '', io_mb: '', pids_max: '' })
  const [notify, setNotify] = useState({ subject: '', body: '' })
  const [jobId, setJobId] = useState(null)
  const [confirmOpen, setConfirmOpen] = useState(false)

  const triggerMut = useMutation({
    mutationFn: () => {
      const action_params = {}
      if (action === 'update_limits') {
        if (limits.cpu_cores !== '') action_params.cpu_pct = Math.round(Number(limits.cpu_cores) * 100)
        if (limits.mem_mb !== '') action_params.mem_mb = Math.round(Number(limits.mem_mb) * 1024)
        for (const k of ['io_mb', 'pids_max']) if (limits[k] !== '') action_params[k] = Number(limits[k])
      }
      if (action === 'notify') { action_params.subject = notify.subject.trim(); action_params.body = notify.body.trim() }
      return post('/api/v1/admin/accounts/bulk-action', { action, usernames: [...selected], action_params })
    },
    onSuccess: (job) => { setConfirmOpen(false); setJobId(job.id); toast.success('Bulk action started', `${selected.size} accounts`) },
    onError: (e) => toast.error('Could not start bulk action', e.message),
  })

  const { data: job } = useQuery({
    queryKey: ['bulk-action', jobId],
    queryFn: () => get(`/api/v1/admin/accounts/bulk-action/${jobId}`),
    enabled: jobId != null,
    refetchInterval: (q) => { const s = q.state.data?.status; return s === 'pending' || s === 'running' ? 1500 : false },
  })
  const finished = job && (job.status === 'completed' || job.status === 'failed')
  const running = triggerMut.isPending || job?.status === 'pending' || job?.status === 'running'
  useEffect(() => { if (finished && jobId) qc.invalidateQueries({ queryKey: ['accounts'] }) }, [finished, jobId, qc])

  return (
    <div className="mb-4 rounded-card border border-accent/40 bg-accent/5 p-4">
      <ConfirmDialog open={confirmOpen} onOpenChange={setConfirmOpen} title={`Apply ${action.replaceAll('_', ' ')} to ${selected.size} accounts?`} description={`Accounts: ${[...selected].join(', ')}. Changes run in order and stop if an account fails. Individual results appear below.`} confirmLabel="Apply to selected accounts" variant={action === 'suspend' ? 'danger' : 'primary'} loading={triggerMut.isPending} onConfirm={() => triggerMut.mutate()} />
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
        <Button loading={running} disabled={running} onClick={() => setConfirmOpen(true)}><Play className="h-4 w-4" /> Apply</Button>
        <Button variant="ghost" disabled={running} onClick={clearSelection}>Clear</Button>
      </div>

      {action === 'update_limits' && (
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {['cpu_cores', 'mem_mb', 'io_mb', 'pids_max'].map((k) => (
            <FormField key={k} label={{cpu_cores:'CPU cores',mem_mb:'Memory (GB)',io_mb:'Disk I/O (MB/s)',pids_max:'Processes'}[k]}>
              <Input type="number" step={k === 'cpu_cores' ? '0.25' : k === 'mem_mb' ? '0.0625' : '1'} placeholder="unchanged" value={limits[k]} onChange={(e) => setLimits((l) => ({ ...l, [k]: e.target.value }))} />
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
  const [selected, setSelected] = useState(() => new Set())
  const listQuery = searchParams.get('q') || ''
  const listPage = Math.max(1, Number.parseInt(searchParams.get('page') || '1', 10) || 1)
  const sortKey = searchParams.get('sort')
  const listSort = sortKey ? { key: sortKey, dir: searchParams.get('dir') === 'desc' ? 'desc' : 'asc' } : null
  useEffect(() => {
    if (searchParams.get('new') !== '1') return
    navigate('/accounts/new', { replace: true })
  }, [navigate, searchParams])

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

  const { data: resellerData } = useQuery({
    queryKey: ['resellers'],
    queryFn: () => get('/api/v1/admin/resellers'),
  })
  const resellers = resellerData?.resellers || []
  const moveReseller = useMutation({
    mutationFn: ({ username, resellerId }) => patch(`/api/v1/admin/resellers/accounts/${username}`, { reseller_id: resellerId || null }),
    onSuccess: () => { toast.success('Reseller assignment updated'); qc.invalidateQueries({ queryKey: ['accounts'] }) },
    onError: error => toast.error('Could not move account', error.message),
  })

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
    nsDescription = 'Restricts each active account’s website and PHP filesystem visibility and uses a private temporary directory. Existing account files are preserved. This enables eligible accounts one at a time, refreshes their website configuration, and stops if verification fails. Terminal and SSH remain separate account-permission boundaries.'
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
    { key: 'reseller', header: 'Reseller', searchable: true, searchValue: r => r.reseller?.username || 'admin', render: r => <Select aria-label={`Reseller for ${r.username}`} value={r.reseller?.id || ''} disabled={moveReseller.isPending} onChange={event => moveReseller.mutate({ username: r.username, resellerId: event.target.value ? Number(event.target.value) : null })}><option value="">Admin owned</option>{resellers.filter(item => item.status === 'active' || item.id === r.reseller?.id).map(item => <option key={item.id} value={item.id}>{item.username}</option>)}</Select> },
    { key: 'created_at', header: 'Created', sortable: true, render: (r) => (r.created_at ? formatDate(r.created_at) : '—') },
    { key: 'actions', header: '', align: 'right', searchable: false, render: r => <div className="flex justify-end gap-1" onClick={event => event.stopPropagation()}><Button variant="secondary" size="sm" onClick={() => navigate(`/accounts/${r.username}`)}>Manage</Button><DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.username}`}><MoreHorizontal className="h-4 w-4"/></Button></DropdownMenuTrigger><DropdownMenuContent>{[['Domains','domains'],['Email','email'],['Backups','backups'],['Security','security'],['Advanced','advanced']].map(([label,tab])=><DropdownMenuItem key={tab} onSelect={()=>navigate(`/accounts/${r.username}?tab=${tab}`)}>{label}</DropdownMenuItem>)}</DropdownMenuContent></DropdownMenu></div> },
  ]

  return (
    <div className="reference-page accounts-list">
      <PageHeader
        title="Accounts"
        description={`Manage all hosting accounts on this server. Boron ${version}.`}
        icon={Users}
      >
        <Button variant="secondary" onClick={() => setNsOpen(true)}>
          <ShieldCheck className="h-4 w-4" /> Enable website isolation
        </Button>
        <Button asChild><Link to="/accounts/new"><Plus className="h-4 w-4" /> Create account</Link></Button>
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
        columnPicker
        className="accounts-reference-table"
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
        emptyAction={<Button asChild><Link to="/accounts/new"><Plus className="h-4 w-4" /> Create account</Link></Button>}
      />

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
