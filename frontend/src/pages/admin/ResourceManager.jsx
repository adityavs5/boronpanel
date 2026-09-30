import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cpu, Gauge, History, AlertTriangle, RotateCcw } from 'lucide-react'
import { del, get, put } from '@/lib/api'
import { formatBytes } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Input, FormField } from '@/components/ui/Input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

const fields = [
  ['cpu_cores', 'CPU cores', .25, .25], ['cpu_weight', 'CPU priority weight', 1, 1],
  ['memory_high_gb', 'Soft memory limit (GB)', .0625, .0625],
  ['memory_max_gb', 'Hard memory limit (GB)', .0625, .0625], ['io_read_iops', 'Read IOPS', 1, 1],
  ['io_write_iops', 'Write IOPS', 1, 1], ['nproc', 'NPROC', 10, 1],
  ['entry_processes', 'Entry processes', 1, 1],
]
const throughputFields = [
  ['io_read_mb', 'Read throughput (MB/s)'], ['io_write_mb', 'Write throughput (MB/s)'],
]

function editValues(row) {
  const values = row.values || {}
  return {
    ...Object.fromEntries(fields.map(([name]) => [name, name.endsWith('_gb') ? '' : values[name] ?? ''])),
    memory_high_gb: values.memory_high_mb == null ? '' : values.memory_high_mb / 1024,
    memory_max_gb: values.memory_max_mb == null ? '' : values.memory_max_mb / 1024,
    io_read_mb: values.io_read_bps == null ? '' : Math.round(values.io_read_bps / 1048576),
    io_write_mb: values.io_write_bps == null ? '' : Math.round(values.io_write_bps / 1048576),
    expires_at: row.policy?.expires_at ? new Date(row.policy.expires_at).toISOString().slice(0, 16) : '',
  }
}

export default function ResourceManager() {
  const qc = useQueryClient()
  const [edit, setEdit] = useState(null)
  const query = useQuery({ queryKey: ['resource-manager'], queryFn: () => get('/api/v1/admin/resources'), refetchInterval: 30000 })
  const save = useMutation({
    mutationFn: ({ username, values }) => put(`/api/v1/admin/resources/accounts/${username}`, {
      ...Object.fromEntries(Object.entries(values).filter(([name]) => !name.endsWith('_gb') && name !== 'io_read_mb' && name !== 'io_write_mb')),
      memory_high_mb: values.memory_high_gb === '' ? null : Math.round(Number(values.memory_high_gb) * 1024),
      memory_max_mb: values.memory_max_gb === '' ? null : Math.round(Number(values.memory_max_gb) * 1024),
      io_read_bps: values.io_read_mb === '' ? null : Number(values.io_read_mb) * 1048576,
      io_write_bps: values.io_write_mb === '' ? null : Number(values.io_write_mb) * 1048576,
      ...Object.fromEntries(fields.filter(([name]) => !name.endsWith('_gb')).map(([name]) => [name, values[name] === '' ? null : Number(values[name])])),
    }),
    onSuccess: () => { toast.success('Resource override applied'); setEdit(null); qc.invalidateQueries({ queryKey: ['resource-manager'] }) },
    onError: error => toast.error('Could not apply limits', error.message),
  })
  const reset = useMutation({
    mutationFn: username => del(`/api/v1/admin/resources/accounts/${username}`),
    onSuccess: () => { toast.success('Account returned to inherited limits'); setEdit(null); qc.invalidateQueries({ queryKey: ['resource-manager'] }) },
    onError: error => toast.error('Could not reset limits', error.message),
  })
  const users = query.data?.users || []
  const byId = Object.fromEntries(users.map(user => [user.account_id, user.username]))
  const userColumns = [
    { key: 'username', header: 'Account', sortable: true, searchable: true, render: row => <button className="font-medium text-accent hover:underline" onClick={() => setEdit({ ...row, form: editValues(row) })}>{row.username}</button> },
    { key: 'cpu', header: 'CPU', render: row => <span>{row.usage ? `${row.usage.cpu_pct.toFixed(1)}%` : '—'} / {row.values.cpu_cores == null ? 'Unlimited' : `${row.values.cpu_cores} cores`}{row.capacity_adjusted && <small className="block text-info">Stored {row.configured_cpu_cores} cores; capped to host capacity</small>}</span> },
    { key: 'memory', header: 'Memory', render: row => <span>{row.usage ? formatBytes(row.usage.memory_bytes) : '—'} / {row.values.memory_max_mb == null ? 'Unlimited' : `${(row.values.memory_max_mb / 1024).toLocaleString(undefined, { maximumFractionDigits: 2 })} GB`}</span> },
    { key: 'pids', header: 'Processes', render: row => `${row.usage?.pids ?? '—'} / ${row.values.nproc ?? 'Unlimited'}` },
    { key: 'source', header: 'Policy source', render: row => <Badge variant={row.policy?.scope_type === 'account' ? 'warning' : 'neutral'}>{row.policy ? `${row.policy.scope_type}:${row.policy.scope_id}` : 'Legacy limits'}</Badge> },
    { key: 'faults', header: 'Recent faults', align: 'right', render: row => row.recent_faults.reduce((sum, fault) => sum + fault.count, 0) || '—' },
    { key: 'actions', header: '', align: 'right', render: row => <Button size="sm" variant="secondary" onClick={() => setEdit({ ...row, form: editValues(row) })}>Manage</Button> },
  ]
  const policyColumns = [
    { key: 'scope_type', header: 'Scope', render: row => <Badge variant="neutral">{row.scope_type}</Badge> },
    { key: 'scope_id', header: 'ID' }, { key: 'version', header: 'Version' },
    { key: 'cpu_cores', header: 'CPU', render: row => row.cpu_cores == null ? 'Unlimited' : `${row.cpu_cores} cores` },
    { key: 'memory_max_mb', header: 'Memory', render: row => row.memory_max_mb == null ? 'Unlimited' : `${(row.memory_max_mb / 1024).toLocaleString(undefined, { maximumFractionDigits: 2 })} GB` },
    { key: 'last_apply_status', header: 'Last apply', render: row => <Badge variant={row.last_apply_status === 'failed' ? 'danger' : 'success'}>{row.last_apply_status || 'Not applied'}</Badge> },
  ]
  const historyColumns = [
    { key: 'sampled_at', header: 'Time', render: row => new Date(row.sampled_at).toLocaleString() },
    { key: 'account_id', header: 'Account', render: row => byId[row.account_id] || `#${row.account_id}` },
    { key: 'cpu_pct', header: 'CPU', render: row => `${row.cpu_pct.toFixed(1)}%` },
    { key: 'memory_bytes', header: 'Memory', render: row => formatBytes(row.memory_bytes) },
    { key: 'pids', header: 'Processes' },
  ]
  const faultColumns = [
    { key: 'detected_at', header: 'Time', render: row => new Date(row.detected_at).toLocaleString() },
    { key: 'account_id', header: 'Account', render: row => byId[row.account_id] || `#${row.account_id}` },
    { key: 'resource', header: 'Limit', render: row => <Badge variant="warning">{row.resource}</Badge> },
    { key: 'count', header: 'Events' }, { key: 'detail', header: 'Detail' },
  ]
  return <div><PageHeader title="Resource Manager" description="Native cgroup and OpenLiteSpeed limits, effective policy sources, usage and faults." icon={Cpu} />
    <Tabs defaultValue="users"><TabsList><TabsTrigger value="users"><Gauge className="h-4 w-4" /> Users</TabsTrigger><TabsTrigger value="plans"><Cpu className="h-4 w-4" /> Plans</TabsTrigger><TabsTrigger value="history"><History className="h-4 w-4" /> History</TabsTrigger><TabsTrigger value="faults"><AlertTriangle className="h-4 w-4" /> Faults</TabsTrigger></TabsList>
      <TabsContent value="users"><DataTable columns={userColumns} data={users} loading={query.isLoading} error={query.error} onRetry={query.refetch} filterable searchPlaceholder="Search accounts…" getRowKey={row => row.account_id} emptyTitle="No hosting accounts" /></TabsContent>
      <TabsContent value="plans"><DataTable columns={policyColumns} data={query.data?.policies || []} loading={query.isLoading} getRowKey={row => row.id} emptyTitle="No resource policies" /></TabsContent>
      <TabsContent value="history"><DataTable columns={historyColumns} data={query.data?.history || []} loading={query.isLoading} getRowKey={(row, index) => `${row.account_id}-${row.sampled_at}-${index}`} pageSize={25} emptyTitle="No resource samples yet" /></TabsContent>
      <TabsContent value="faults"><DataTable columns={faultColumns} data={query.data?.faults || []} loading={query.isLoading} getRowKey={(row, index) => `${row.account_id}-${row.detected_at}-${index}`} pageSize={25} emptyTitle="No limit faults recorded" /></TabsContent>
    </Tabs>
    <Dialog open={!!edit} onOpenChange={open => !open && setEdit(null)}><DialogContent size="lg"><DialogHeader><DialogTitle>Resource limits for {edit?.username}</DialogTitle><DialogDescription>Blank means Unlimited. Soft memory starts reclaiming and throttling before the hard limit. This server exposes {query.data?.host?.cpu_cores ?? '…'} logical CPU cores.</DialogDescription></DialogHeader>{edit && <form onSubmit={event => { event.preventDefault(); save.mutate({ username: edit.username, values: edit.form }) }}><DialogBody className="grid gap-4 sm:grid-cols-2"><p className="sm:col-span-2 text-sm text-muted-foreground">Host RAM: {query.data?.host?.memory_gb?.toFixed(1) || '…'} GB. Memory limits do not reserve host RAM.</p>{Number(edit.form.memory_max_gb) > Number(query.data?.host?.memory_gb || Infinity) && <p role="status" className="sm:col-span-2 text-sm text-warning">This limit exceeds host memory. Concurrent usage may exhaust available RAM.</p>}{fields.map(([name, label, min, step]) => <FormField key={name} label={label} hint={name === 'memory_high_gb' ? 'The account can continue above this level until its hard limit.' : undefined}><Input type="number" min={min} max={name === 'cpu_cores' ? query.data?.host?.cpu_cores : undefined} step={step} placeholder="Unlimited" value={edit.form[name]} onChange={event => setEdit(current => ({ ...current, form: { ...current.form, [name]: event.target.value } }))} /></FormField>)}{throughputFields.map(([name, label]) => <FormField key={name} label={label}><Input type="number" min="1" step="1" placeholder="Unlimited" value={edit.form[name]} onChange={event => setEdit(current => ({ ...current, form: { ...current.form, [name]: event.target.value } }))} /></FormField>)}<FormField label="Temporary override expires" hint="Leave blank for no expiry."><Input type="datetime-local" value={edit.form.expires_at} onChange={event => setEdit(current => ({ ...current, form: { ...current.form, expires_at: event.target.value } }))} /></FormField></DialogBody><DialogFooter><Button type="button" variant="ghost" loading={reset.isPending} onClick={() => reset.mutate(edit.username)}><RotateCcw className="h-4 w-4" /> Reset to plan</Button><Button type="button" variant="secondary" onClick={() => setEdit(null)}>Cancel</Button><Button type="submit" loading={save.isPending}>Apply limits</Button></DialogFooter></form>}</DialogContent></Dialog>
  </div>
}
