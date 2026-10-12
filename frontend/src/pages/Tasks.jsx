import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock3, ExternalLink, RotateCcw, Square, ListChecks } from 'lucide-react'
import { get, post } from '@/lib/api'
import { useAuth } from '@/store/auth'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

function when(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function elapsed(seconds) {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

export default function Tasks() {
  const role = useAuth((state) => state.role)
  const qc = useQueryClient()
  const [filters, setFilters] = useState({ query: '', status: '', kind: '', account: '', date_from: '', date_to: '' })
  const [selected, setSelected] = useState(null)
  const [action, setAction] = useState(null)
  const params = useMemo(() => {
    const search = new URLSearchParams({ limit: '250' })
    Object.entries(filters).forEach(([key, value]) => { if (value) search.set(key, value) })
    return search.toString()
  }, [filters])
  const tasksQuery = useQuery({
    queryKey: ['tasks', params],
    queryFn: () => get(`/api/v1/tasks?${params}`),
    refetchInterval: (query) => (query.state.data?.tasks || []).some((row) => ['queued', 'running'].includes(row.status)) ? 2500 : false,
  })
  const detailsQuery = useQuery({
    queryKey: ['task', selected?.kind, selected?.id],
    queryFn: () => get(`/api/v1/tasks/${selected.kind}/${selected.id}`),
    enabled: !!selected,
    refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.status) ? 2000 : false,
  })
  const actionMutation = useMutation({
    mutationFn: ({ task, action: verb }) => post(`/api/v1/tasks/${task.kind}/${task.id}/${verb}`, {}),
    onSuccess: (_, variables) => {
      toast.success(variables.action === 'retry' ? 'Retry queued' : 'Cancellation requested')
      setAction(null)
      qc.invalidateQueries({ queryKey: ['tasks'] })
      qc.invalidateQueries({ queryKey: ['task', variables.task.kind, variables.task.id] })
    },
  })
  const rows = tasksQuery.data?.tasks || []
  const accounts = [...new Set(rows.map((row) => row.account).filter(Boolean))].sort()
  const kinds = (tasksQuery.data?.kinds || []).map((kind) => ({ kind, label: rows.find((row) => row.kind === kind)?.label || kind.replaceAll('-', ' ') }))
  const update = (key, value) => setFilters((current) => ({ ...current, [key]: value }))
  const columns = [
    { key: 'label', header: 'Task', sortable: true, render: (row) => <div><div className="font-medium">{row.label}</div><div className="text-xs text-muted-foreground">{row.key}</div></div> },
    ...(role === 'admin' ? [{ key: 'account', header: 'Account', sortable: true, render: (row) => row.account || 'Server' }] : []),
    { key: 'status', header: 'Status', sortable: true, render: (row) => <StatusBadge status={row.status} /> },
    { key: 'progress', header: 'Progress', render: (row) => <div className="min-w-28"><span>{row.progress == null ? 'In progress' : `${row.progress}%`}</span>{row.progress != null && <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-accent" style={{ width: `${row.progress}%` }} /></div>}</div> },
    { key: 'started_at', header: 'Started', sortable: true, render: (row) => when(row.started_at) },
    { key: 'duration_seconds', header: 'Duration', sortable: true, render: (row) => elapsed(row.duration_seconds) },
    { key: 'actions', header: '', render: (row) => <div className="flex justify-end gap-2"><Button size="sm" variant="secondary" onClick={(event) => { event.stopPropagation(); setSelected(row) }}>Details</Button>{row.can_retry && <Button size="sm" variant="secondary" onClick={(event) => { event.stopPropagation(); setAction({ task: row, action: 'retry' }) }}><RotateCcw className="h-4 w-4" />Retry</Button>}{row.can_cancel && <Button size="sm" variant="secondary" onClick={(event) => { event.stopPropagation(); setAction({ task: row, action: 'cancel' }) }}><Square className="h-4 w-4" />Cancel</Button>}</div> },
  ]
  const toolbar = <div className="flex flex-wrap items-center gap-2">
    <Input aria-label="Search tasks" placeholder="Search tasks" value={filters.query} onChange={(event) => update('query', event.target.value)} className="w-48" />
    <Select aria-label="Filter by status" value={filters.status} onChange={(event) => update('status', event.target.value)}><option value="">All statuses</option>{['queued', 'running', 'completed', 'failed', 'cancelled'].map((value) => <option key={value} value={value}>{value}</option>)}</Select>
    <Select aria-label="Filter by task type" value={filters.kind} onChange={(event) => update('kind', event.target.value)}><option value="">All task types</option>{kinds.map((item) => <option key={item.kind} value={item.kind}>{item.label}</option>)}</Select>
    {role === 'admin' && <Select aria-label="Filter by account" value={filters.account} onChange={(event) => update('account', event.target.value)}><option value="">All accounts</option>{accounts.map((account) => <option key={account} value={account}>{account}</option>)}</Select>}
    <Input type="date" aria-label="Tasks from date" value={filters.date_from} onChange={(event) => update('date_from', event.target.value)} />
    <Input type="date" aria-label="Tasks through date" value={filters.date_to} onChange={(event) => update('date_to', event.target.value)} />
  </div>

  const detail = detailsQuery.data
  return <div>
    <PageHeader title="Tasks" description={role === 'admin' ? 'Progress and results for panel operations across the server.' : 'Progress and results for operations on your hosting account.'} icon={ListChecks} />
    <DataTable columns={columns} data={rows} loading={tasksQuery.isLoading} error={tasksQuery.error} onRetry={tasksQuery.refetch} toolbar={toolbar} columnPicker pageSize={25} initialSort={{ key: 'started_at', dir: 'desc' }} getRowKey={(row) => row.key} onRowClick={setSelected} emptyTitle="No tasks found" emptyDescription="Operations such as backups, restores, installs and migrations will appear here." emptyIcon={Clock3} />

    <Dialog open={!!selected} onOpenChange={(open) => { if (!open) setSelected(null) }}>
      <DialogContent size="lg">
        <DialogHeader><DialogTitle>{detail?.label || selected?.label || 'Task details'}</DialogTitle></DialogHeader>
        <DialogBody className="space-y-5">
          {detailsQuery.isLoading ? <p role="status" className="text-sm text-muted-foreground">Loading task details…</p> : detailsQuery.error ? <p role="alert" className="text-sm text-danger">{detailsQuery.error.message}</p> : detail && <>
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
              {[['Status', <StatusBadge status={detail.status} />], ['Account', detail.account || 'Server'], ['Started', when(detail.started_at)], ['Duration', elapsed(detail.duration_seconds)], ['Request ID', detail.request_id || '—'], ['Error reference', detail.error_reference || '—']].map(([label, value]) => <div key={label}><dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</dt><dd className="mt-1 break-all text-foreground">{value}</dd></div>)}
            </dl>
            <div><h3 className="text-sm font-semibold">Current detail</h3><p className="mt-1 text-sm text-muted-foreground">{detail.detail}</p></div>
            <div><h3 className="text-sm font-semibold">Action timeline</h3>{detail.timeline?.length ? <ol className="mt-2 divide-y divide-border border-y border-border">{detail.timeline.map((step, index) => <li key={`${step.name}-${index}`} className="py-3"><div className="flex items-center justify-between gap-3"><span className="text-sm font-medium">{step.name}</span><StatusBadge status={step.result} /></div><p className="mt-1 text-xs text-muted-foreground">{step.duration_ms} ms{step.exit_code == null ? '' : ` · exit ${step.exit_code}`}</p>{(step.stdout || step.stderr) && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-control bg-muted p-3 text-xs">{[step.stdout, step.stderr].filter(Boolean).join('\n')}</pre>}</li>)}</ol> : <p className="mt-1 text-sm text-muted-foreground">No step timeline was recorded for this task.</p>}</div>
          </>}
        </DialogBody>
        <DialogFooter>{detail?.feature_url && <Button variant="secondary" asChild><Link to={detail.feature_url}>Open feature <ExternalLink className="h-4 w-4" /></Link></Button>}<Button onClick={() => setSelected(null)}>Done</Button></DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog open={!!action} onOpenChange={(open) => { if (!open && !actionMutation.isPending) setAction(null) }}>
      <DialogContent size="sm" showClose={!actionMutation.isPending}>
        <DialogHeader><DialogTitle>{action?.action === 'retry' ? 'Retry task?' : 'Cancel task?'}</DialogTitle></DialogHeader>
        <DialogBody><p className="text-sm text-muted-foreground">{action?.task?.label} {action?.task?.key} will be {action?.action === 'retry' ? 'queued again using its existing safe retry path' : 'asked to stop at its next supported checkpoint'}.</p>{actionMutation.error && <p role="alert" className="mt-3 text-sm text-danger">{actionMutation.error.message}</p>}</DialogBody>
        <DialogFooter><Button variant="secondary" disabled={actionMutation.isPending} onClick={() => setAction(null)}>Back</Button><Button loading={actionMutation.isPending} onClick={() => actionMutation.mutate(action)}>{action?.action === 'retry' ? 'Retry' : 'Cancel'}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
}
