import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Database, Skull, RefreshCw, Gauge, HardDrive, Users, ShieldAlert } from 'lucide-react'
import { get, del, post } from '@/lib/api'
import { formatBytes } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { toast } from '@/components/ui/Toast'

// Missing-features batch, goal feature 7: live MariaDB monitor, admin-only,
// auto-refreshed every 10s (the goal's own explicit cadence) -- separate
// from the existing Slow Queries page (that's a historical log browser;
// this is "what's happening right now").
export default function DbMonitor() {
  const qc = useQueryClient()
  const [toKill, setToKill] = useState(null)
  const [grantOpen, setGrantOpen] = useState(false)

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['db-monitor'],
    queryFn: () => get('/api/v1/admin/db/monitor'),
    refetchInterval: 10000,
  })

  const killMut = useMutation({
    mutationFn: (threadId) => del(`/api/v1/admin/db/queries/${threadId}`),
    onSuccess: (_r, threadId) => { toast.success('Query killed', `Thread ${threadId}`); setToKill(null); qc.invalidateQueries({ queryKey: ['db-monitor'] }) },
    onError: (e) => { toast.error('Could not kill query', e.message); setToKill(null) },
  })

  const grantMut = useMutation({
    mutationFn: () => post('/api/v1/admin/db/kill-privilege/bootstrap', { confirm: true }),
    onSuccess: () => { toast.success('Privilege granted', 'forgehost_daemon can now kill any account’s query'); setGrantOpen(false); qc.invalidateQueries({ queryKey: ['db-monitor'] }) },
    onError: (e) => { toast.error('Could not grant privilege', e.message); setGrantOpen(false) },
  })

  if (isLoading) return <CardSkeleton />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  const processColumns = [
    { key: 'id', header: 'Thread', sortable: true, render: (r) => <span className="font-mono text-xs">{r.id}</span> },
    { key: 'username', header: 'Account', render: (r) => r.username ? <Badge variant="outline">{r.username}</Badge> : <span className="text-muted-foreground">—</span> },
    { key: 'user', header: 'DB User', render: (r) => <span className="font-mono text-xs">{r.user}</span> },
    { key: 'db', header: 'Database', render: (r) => <span className="font-mono text-xs">{r.db || '—'}</span> },
    { key: 'command', header: 'Command' },
    { key: 'time_seconds', header: 'Time', align: 'right', sortable: true, sortValue: (r) => r.time_seconds, render: (r) => `${r.time_seconds}s` },
    { key: 'state', header: 'State', render: (r) => <span className="text-xs text-muted-foreground">{r.state || '—'}</span> },
    { key: 'info', header: 'Query', render: (r) => <span className="line-clamp-1 break-all font-mono text-xs" title={r.info || ''}>{r.info || '—'}</span> },
    {
      key: 'actions', header: '', align: 'right', render: (r) => (
        <Button variant="ghost" size="icon-sm" title="Kill query" onClick={() => setToKill(r)}>
          <Skull className="h-4 w-4 text-danger" />
        </Button>
      ),
    },
  ]

  const slowColumns = [
    { key: 'start_time', header: 'Time', render: (r) => <span className="whitespace-nowrap font-mono text-xs">{r.start_time}</span> },
    { key: 'db', header: 'Database', render: (r) => <span className="font-mono text-xs">{r.db || '—'}</span> },
    { key: 'query_time_seconds', header: 'Duration', align: 'right', sortable: true, sortValue: (r) => r.query_time_seconds, render: (r) => `${r.query_time_seconds.toFixed(2)}s` },
    { key: 'rows_examined', header: 'Rows examined', align: 'right', render: (r) => r.rows_examined.toLocaleString() },
    { key: 'sql_text', header: 'Query', render: (r) => <span className="line-clamp-1 break-all font-mono text-xs" title={r.sql_text}>{r.sql_text}</span> },
  ]

  return (
    <div>
      <PageHeader title="Database Monitor" description="Live MariaDB activity across every hosted account. Auto-refreshes every 10 seconds." icon={Database}>
        <Button variant="secondary" onClick={() => refetch()} loading={isFetching}><RefreshCw className="h-4 w-4" /> Refresh</Button>
      </PageHeader>

      {data.kill_privilege && !data.kill_privilege.granted && (
        <Card className="mb-6 border-warning/40 bg-warning/5">
          <CardContent className="flex items-center justify-between gap-4 py-4">
            <div className="flex items-center gap-3">
              <ShieldAlert className="h-6 w-6 text-warning" />
              <div>
                <p className="font-medium text-foreground">Kill can only stop forgehost_daemon's own connections right now</p>
                <p className="text-sm text-muted-foreground">Grant {data.kill_privilege.privilege} once to let the kill button stop any hosted account's query too.</p>
              </div>
            </div>
            <Button variant="outline" onClick={() => setGrantOpen(true)}>Enable kill for all accounts</Button>
          </CardContent>
        </Card>
      )}

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Card>
          <CardContent className="flex items-center gap-3 py-4">
            <Gauge className="h-8 w-8 text-accent" />
            <div>
              <p className="text-2xl font-semibold text-foreground">{data.connections.total_connections}<span className="text-sm font-normal text-muted-foreground"> / {data.connections.max_connections}</span></p>
              <p className="text-xs text-muted-foreground">Connections</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-4">
            <HardDrive className="h-8 w-8 text-success" />
            <div>
              <p className="text-2xl font-semibold text-foreground">{formatBytes(data.db_sizes.server_total_bytes)}</p>
              <p className="text-xs text-muted-foreground">Total DB size</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-3 py-4">
            <Users className="h-8 w-8 text-info" />
            <div>
              <p className="text-2xl font-semibold text-foreground">{data.db_sizes.accounts.length}</p>
              <p className="text-xs text-muted-foreground">Accounts with databases</p>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Running queries</CardTitle>
          <CardDescription>SHOW FULL PROCESSLIST — every connection to this MariaDB instance, right now.</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable columns={processColumns} data={data.processes} getRowKey={(r) => r.id} pageSize={20}
            emptyTitle="No active queries" emptyDescription="Nothing is running right now." emptyIcon={Database} />
        </CardContent>
      </Card>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Slow queries — last hour</CardTitle>
          <CardDescription>From mysql.slow_log, scoped to the last {data.slow_queries_lookback_hours} hour(s). For full history, see the Slow Queries page.</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable columns={slowColumns} data={data.slow_queries} getRowKey={(r, i) => `${r.thread_id}-${i}`} pageSize={10}
            emptyTitle="No slow queries" emptyDescription="Nothing slow in the last hour." emptyIcon={Gauge} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Database sizes by account</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {data.db_sizes.accounts.length === 0 ? (
            <p className="text-sm text-muted-foreground">No hosted databases yet.</p>
          ) : (
            data.db_sizes.accounts.map((a) => (
              <div key={a.username} className="flex items-center justify-between rounded-btn border border-border px-3 py-2 text-sm">
                <span className="font-medium text-foreground">{a.username}</span>
                <span className="text-muted-foreground">{a.databases.length} database{a.databases.length === 1 ? '' : 's'} · {formatBytes(a.total_bytes)}</span>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={!!toKill}
        onOpenChange={(o) => !o && setToKill(null)}
        title={toKill ? `Kill thread ${toKill.id}?` : ''}
        description={toKill ? `${toKill.username ? `Account: ${toKill.username}. ` : ''}${toKill.info || toKill.command}` : ''}
        confirmLabel="Kill query"
        variant="danger"
        loading={killMut.isPending}
        onConfirm={() => killMut.mutate(toKill.id)}
      />

      <ConfirmDialog
        open={grantOpen}
        onOpenChange={setGrantOpen}
        title="Grant CONNECTION_ADMIN to forgehost_daemon?"
        description="Lets the panel's daemon kill any hosted account's query, not just its own. Takes effect immediately, no service restart."
        confirmLabel="Grant privilege"
        loading={grantMut.isPending}
        onConfirm={() => grantMut.mutate()}
      />
    </div>
  )
}
