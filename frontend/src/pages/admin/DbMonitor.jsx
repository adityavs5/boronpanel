import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Database, Skull, RefreshCw, Gauge, HardDrive, Users, ShieldAlert, SlidersHorizontal } from 'lucide-react'
import { get, del, post, patch, put } from '@/lib/api'
import { formatBytes } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { ConfirmDialog, Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter } from '@/components/ui/Dialog'
import { ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import { FormField, Input } from '@/components/ui/Input'
import { toast } from '@/components/ui/Toast'

const selectClass = 'flex h-9 w-full rounded-btn border border-input bg-input-surface px-3 text-sm text-foreground focus-visible:outline-none focus-visible:border-accent focus-visible:ring-[3px] focus-visible:ring-ring/25'
const blankPolicy = { mode: 'monitor', max_user_connections: '', max_queries_per_hour: '', max_updates_per_hour: '', max_connections_per_hour: '', max_statement_time: '', warning_threshold_pct: 80, cooldown_seconds: 300 }

export default function DbMonitor() {
  const qc = useQueryClient()
  const [toKill, setToKill] = useState(null)
  const [grantOpen, setGrantOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const monitor = useQuery({ queryKey: ['db-monitor'], queryFn: () => get('/api/v1/admin/db/monitor'), refetchInterval: 10000 })
  const governor = useQuery({ queryKey: ['db-governor'], queryFn: () => get('/api/v1/admin/db/governor'), refetchInterval: 15000 })
  const refresh = () => { monitor.refetch(); governor.refetch() }
  const invalidate = () => { qc.invalidateQueries({ queryKey: ['db-monitor'] }); qc.invalidateQueries({ queryKey: ['db-governor'] }) }
  const killMut = useMutation({ mutationFn: id => del(`/api/v1/admin/db/queries/${id}`), onSuccess: () => { toast.success('Query stopped'); setToKill(null); invalidate() }, onError: e => toast.error('Could not stop query', e.message) })
  const grantMut = useMutation({ mutationFn: () => post('/api/v1/admin/db/kill-privilege/bootstrap', { confirm: true }), onSuccess: () => { toast.success('Query control enabled'); setGrantOpen(false); invalidate() }, onError: e => toast.error('Could not grant privilege', e.message) })
  const userstatMut = useMutation({ mutationFn: () => post('/api/v1/admin/db/governor/userstat', { confirm: true }), onSuccess: () => { toast.success('MariaDB user statistics enabled'); invalidate() }, onError: e => toast.error('Could not enable statistics', e.message) })
  const modeMut = useMutation({ mutationFn: mode => patch('/api/v1/admin/db/governor/mode', { mode }), onSuccess: () => { toast.success('Governor mode updated'); invalidate() }, onError: e => toast.error('Could not change governor mode', e.message) })
  const policyMut = useMutation({
    mutationFn: ({ username, values }) => put(`/api/v1/admin/db/governor/accounts/${username}`, Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value === '' ? null : value]))),
    onSuccess: r => { toast.success(r.applied ? 'Database limits applied' : 'Monitor policy saved'); setEditing(null); invalidate() },
    onError: e => toast.error('Could not save policy', e.message),
  })
  const resetMut = useMutation({ mutationFn: username => del(`/api/v1/admin/db/governor/accounts/${username}`), onSuccess: () => { toast.success('Database limits reset'); invalidate() }, onError: e => toast.error('Could not reset policy', e.message) })

  if (monitor.isLoading || governor.isLoading) return <CardSkeleton />
  if (monitor.error) return <ErrorState error={monitor.error} onRetry={monitor.refetch} />
  const data = monitor.data
  const gov = governor.data || { accounts: [], events: [], statistics: { status: {}, users: [] }, global_mode: 'monitor' }
  const processColumns = [
    { key: 'id', header: 'Thread', render: r => <span className="font-mono text-xs">{r.id}</span> },
    { key: 'username', header: 'Account', render: r => r.username ? <Badge variant="outline">{r.username}</Badge> : '—' },
    { key: 'user', header: 'DB user', render: r => <span className="font-mono text-xs">{r.user}</span> },
    { key: 'db', header: 'Database', render: r => <span className="font-mono text-xs">{r.db || '—'}</span> },
    { key: 'time_seconds', header: 'Time', align: 'right', render: r => `${r.time_seconds}s` },
    { key: 'info', header: 'Query', render: r => <span className="line-clamp-1 break-all font-mono text-xs" title={r.info || ''}>{r.info || r.command}</span> },
    { key: 'actions', header: '', align: 'right', render: r => <Button variant="ghost" size="icon-sm" title="Stop hosted query" disabled={!r.username} onClick={() => setToKill(r)}><Skull className="h-4 w-4 text-danger" /></Button> },
  ]
  const userColumns = [
    { key: 'username', header: 'Account' }, { key: 'db_user', header: 'Database user', render: r => <span className="font-mono text-xs">{r.db_user}</span> },
    { key: 'concurrent_connections', header: 'Connections', align: 'right' },
    { key: 'busy_seconds', header: 'Busy time', align: 'right', render: r => `${r.busy_seconds.toFixed(2)}s` },
    { key: 'cpu_seconds', header: 'CPU', align: 'right', render: r => `${r.cpu_seconds.toFixed(2)}s` },
    { key: 'traffic', header: 'Traffic', align: 'right', render: r => formatBytes(r.bytes_received + r.bytes_sent) },
    { key: 'rows_read', header: 'Rows read', align: 'right', render: r => r.rows_read.toLocaleString() },
  ]
  const eventColumns = [
    { key: 'created_at', header: 'Time', render: r => new Date(r.created_at).toLocaleString() }, { key: 'username', header: 'Account' },
    { key: 'event_type', header: 'Event' }, { key: 'action', header: 'Action', render: r => <Badge variant="outline">{r.action.replaceAll('_', ' ')}</Badge> },
    { key: 'detail', header: 'Detail' },
  ]

  return <div>
    <PageHeader title="Database Monitor" description="Live queries, account activity, and safe native MariaDB limits." icon={Database}>
      <Button variant="secondary" onClick={refresh} loading={monitor.isFetching || governor.isFetching}><RefreshCw className="h-4 w-4" />Refresh</Button>
    </PageHeader>
    <Tabs defaultValue="overview">
      <TabsList><TabsTrigger value="overview">Overview</TabsTrigger><TabsTrigger value="queries">Live queries</TabsTrigger><TabsTrigger value="users">Users</TabsTrigger><TabsTrigger value="policies">Governor policies</TabsTrigger><TabsTrigger value="events">Events</TabsTrigger></TabsList>
      <TabsContent value="overview">
        <div className="grid gap-4 sm:grid-cols-3">
          <Metric icon={Gauge} value={`${data.connections.total_connections} / ${data.connections.max_connections}`} label="Connections" />
          <Metric icon={HardDrive} value={formatBytes(data.db_sizes.server_total_bytes)} label="Hosted database storage" />
          <Metric icon={Users} value={data.db_sizes.accounts.length} label="Accounts with databases" />
        </div>
        <Card className="mt-5"><CardHeader><CardTitle>Database storage by account</CardTitle></CardHeader><CardContent className="space-y-2">{data.db_sizes.accounts.map(a => <div key={a.username} className="flex justify-between rounded-btn border border-border px-3 py-2 text-sm"><strong>{a.username}</strong><span className="text-muted-foreground">{a.databases.length} databases · {formatBytes(a.total_bytes)}</span></div>)}</CardContent></Card>
      </TabsContent>
      <TabsContent value="queries">
        {!data.kill_privilege?.granted && <Notice icon={ShieldAlert} title="Query stop permission is disabled" text={`Grant ${data.kill_privilege?.privilege} once to stop a runaway hosted query.`}><Button variant="outline" onClick={() => setGrantOpen(true)}>Enable query control</Button></Notice>}
        <Card><CardHeader><CardTitle>Running queries</CardTitle><CardDescription>Only queries attributed to a Boron-managed customer database can be stopped.</CardDescription></CardHeader><CardContent><DataTable columns={processColumns} data={data.processes} getRowKey={r => r.id} pageSize={20} emptyTitle="No active queries" emptyDescription="Nothing is running right now." emptyIcon={Database} /></CardContent></Card>
      </TabsContent>
      <TabsContent value="users">
        {!gov.statistics?.status?.enabled && <Notice icon={Gauge} title="Per-user statistics are off" text="MariaDB userstat adds lightweight counters for connections, rows, bytes, busy time and CPU time."><Button onClick={() => userstatMut.mutate()} loading={userstatMut.isPending}>Enable user statistics</Button></Notice>}
        <Card><CardHeader><CardTitle>Hosted database users</CardTitle><CardDescription>System and Boron service users are intentionally excluded.</CardDescription></CardHeader><CardContent><DataTable columns={userColumns} data={gov.statistics?.users || []} getRowKey={r => r.db_user} pageSize={20} emptyTitle="No statistics yet" emptyDescription="Enable user statistics or wait for customer database activity." emptyIcon={Users} /></CardContent></Card>
      </TabsContent>
      <TabsContent value="policies">
        <Card className="mb-5"><CardHeader><CardTitle>Global mode</CardTitle><CardDescription>Monitor records activity without changing accounts. Enforce applies only account policies also set to Enforce. Pause stops all automatic enforcement.</CardDescription></CardHeader><CardContent><div className="flex flex-wrap gap-2">{['paused', 'monitor', 'enforce'].map(mode => <Button key={mode} variant={gov.global_mode === mode ? 'primary' : 'secondary'} onClick={() => modeMut.mutate(mode)}>{mode[0].toUpperCase() + mode.slice(1)}</Button>)}</div></CardContent></Card>
        <Card><CardHeader><CardTitle>Account policies</CardTitle><CardDescription>MariaDB can limit connections, query counts and statement duration. It cannot provide the smooth per-query CPU or disk-I/O throttling of CloudLinux MySQL Governor.</CardDescription></CardHeader><CardContent className="space-y-2">{gov.accounts.map(account => <div key={account.id} className="flex flex-col gap-3 rounded-btn border border-border p-3 md:flex-row md:items-center md:justify-between"><div><strong>{account.username}</strong><p className="text-xs text-muted-foreground">{account.database_users.join(', ')} · {account.policy.id ? account.policy.mode : 'Inherited monitor defaults'}</p></div><div className="flex gap-2"><Button variant="secondary" onClick={() => setEditing({ username: account.username, values: { ...blankPolicy, ...Object.fromEntries(Object.entries(account.policy).map(([k, v]) => [k, v ?? ''])) } })}>Configure</Button>{account.policy.id && <Button variant="ghost" onClick={() => resetMut.mutate(account.username)}>Reset</Button>}</div></div>)}</CardContent></Card>
      </TabsContent>
      <TabsContent value="events"><Card><CardHeader><CardTitle>Governor events</CardTitle></CardHeader><CardContent><DataTable columns={eventColumns} data={gov.events} getRowKey={r => r.id} pageSize={25} emptyTitle="No governor events" emptyDescription="Policy changes and automatic actions appear here." emptyIcon={SlidersHorizontal} /></CardContent></Card></TabsContent>
    </Tabs>
    <ConfirmDialog open={!!toKill} onOpenChange={open => !open && setToKill(null)} title={toKill ? `Stop thread ${toKill.id}?` : ''} description={toKill?.info || ''} confirmLabel="Stop query" variant="danger" loading={killMut.isPending} onConfirm={() => killMut.mutate(toKill.id)} />
    <ConfirmDialog open={grantOpen} onOpenChange={setGrantOpen} title="Enable hosted-query control?" description="Grants CONNECTION_ADMIN to Boron's local database service account. Boron still refuses to stop system or unowned queries." confirmLabel="Enable" loading={grantMut.isPending} onConfirm={() => grantMut.mutate()} />
    {editing && <PolicyDialog state={editing} setState={setEditing} saving={policyMut.isPending} onSave={() => policyMut.mutate(editing)} />}
  </div>
}

function Metric({ icon: Icon, value, label }) { return <Card><CardContent className="flex items-center gap-3 py-4"><Icon className="h-8 w-8 text-accent" /><div><p className="text-2xl font-semibold">{value}</p><p className="text-xs text-muted-foreground">{label}</p></div></CardContent></Card> }
function Notice({ icon: Icon, title, text, children }) { return <Card className="mb-5 border-warning/40 bg-warning/5"><CardContent className="flex flex-col justify-between gap-4 py-4 sm:flex-row sm:items-center"><div className="flex gap-3"><Icon className="h-6 w-6 text-warning" /><div><p className="font-medium">{title}</p><p className="text-sm text-muted-foreground">{text}</p></div></div>{children}</CardContent></Card> }
function PolicyDialog({ state, setState, saving, onSave }) {
  const update = (key, value) => setState(current => ({ ...current, values: { ...current.values, [key]: value } }))
  return <Dialog open onOpenChange={open => !open && setState(null)}><DialogContent size="lg"><DialogHeader><DialogTitle>Database policy · {state.username}</DialogTitle><DialogDescription>Blank fields mean Unlimited. Limits apply to every database user owned by this account.</DialogDescription></DialogHeader><DialogBody className="grid gap-4 sm:grid-cols-2"><FormField label="Policy mode"><select className={selectClass} value={state.values.mode} onChange={e => update('mode', e.target.value)}><option value="monitor">Monitor only</option><option value="enforce">Enforce when global mode is Enforce</option></select></FormField><FormField label="Concurrent connections"><Input type="number" min="1" placeholder="Unlimited" value={state.values.max_user_connections} onChange={e => update('max_user_connections', e.target.value ? Number(e.target.value) : '')} /></FormField><FormField label="Queries per hour"><Input type="number" min="1" placeholder="Unlimited" value={state.values.max_queries_per_hour} onChange={e => update('max_queries_per_hour', e.target.value ? Number(e.target.value) : '')} /></FormField><FormField label="Updates per hour"><Input type="number" min="1" placeholder="Unlimited" value={state.values.max_updates_per_hour} onChange={e => update('max_updates_per_hour', e.target.value ? Number(e.target.value) : '')} /></FormField><FormField label="Connections per hour"><Input type="number" min="1" placeholder="Unlimited" value={state.values.max_connections_per_hour} onChange={e => update('max_connections_per_hour', e.target.value ? Number(e.target.value) : '')} /></FormField><FormField label="Maximum statement time" hint="Seconds; support depends on MariaDB build."><Input type="number" min="0.1" step="0.1" placeholder="Unlimited" value={state.values.max_statement_time} onChange={e => update('max_statement_time', e.target.value ? Number(e.target.value) : '')} /></FormField><FormField label="Warning threshold (%)"><Input type="number" min="1" max="100" value={state.values.warning_threshold_pct} onChange={e => update('warning_threshold_pct', Number(e.target.value))} /></FormField><FormField label="Cooldown (seconds)"><Input type="number" min="30" max="86400" value={state.values.cooldown_seconds} onChange={e => update('cooldown_seconds', Number(e.target.value))} /></FormField></DialogBody><DialogFooter><Button variant="secondary" onClick={() => setState(null)}>Cancel</Button><Button onClick={onSave} loading={saving}>Save policy</Button></DialogFooter></DialogContent></Dialog>
}
