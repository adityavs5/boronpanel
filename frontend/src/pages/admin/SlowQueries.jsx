import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ListChecks, Database, Clock, Zap, RotateCw } from 'lucide-react'
import { get, post } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatNumber } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Spinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

const LIMIT_OPTIONS = [50, 100, 250, 500]

export default function SlowQueries() {
  // useAccountUsername keeps the query key stable across admin / per-account contexts.
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [bootstrapOpen, setBootstrapOpen] = useState(false)
  const [selected, setSelected] = useState(null) // row whose full query is shown
  const [draft, setDraft] = useState({ db: '', q: '', limit: 100 }) // filter form inputs
  const [applied, setApplied] = useState({ db: '', q: '', limit: 100 }) // filters sent to the server

  const statusQuery = useQuery({
    queryKey: ['slow-queries-status', username],
    queryFn: () => get('/api/v1/mysql/slow-queries/status'),
  })
  const status = statusQuery.data
  const enabled = !!status?.enabled

  const queriesQuery = useQuery({
    queryKey: ['slow-queries', username, applied.db, applied.q, applied.limit],
    queryFn: () => {
      const params = new URLSearchParams()
      if (applied.db) params.set('db', applied.db)
      if (applied.q) params.set('q', applied.q)
      params.set('limit', String(applied.limit))
      return get(`/api/v1/mysql/slow-queries?${params.toString()}`)
    },
    enabled,
  })

  const bootstrapMut = useMutation({
    mutationFn: () => post('/api/v1/mysql/slow-queries/bootstrap', { confirm: true }),
    onSuccess: () => {
      toast.success('Slow query log enabled', 'MariaDB now records queries slower than the threshold.')
      qc.invalidateQueries({ queryKey: ['slow-queries-status', username] })
      qc.invalidateQueries({ queryKey: ['slow-queries', username] })
      setBootstrapOpen(false)
    },
    onError: (e) => toast.error('Could not enable slow query log', e.message),
  })

  function applyFilters(e) {
    e.preventDefault()
    setApplied({ db: draft.db.trim(), q: draft.q.trim(), limit: draft.limit })
  }
  function clearFilters() {
    const reset = { db: '', q: '', limit: 100 }
    setDraft(reset)
    setApplied(reset)
  }

  const columns = [
    {
      key: 'start_time',
      header: 'Time',
      sortable: true,
      searchable: true,
      render: (r) => (
        <span className="whitespace-nowrap font-mono text-xs text-muted-foreground">{r.start_time || '—'}</span>
      ),
    },
    {
      key: 'db',
      header: 'Database',
      sortable: true,
      searchable: true,
      render: (r) =>
        r.db ? <span className="text-foreground">{r.db}</span> : <span className="text-muted-foreground">—</span>,
    },
    {
      key: 'query_time_seconds',
      header: 'Query time (s)',
      align: 'right',
      sortable: true,
      sortValue: (r) => r.query_time_seconds ?? 0,
      render: (r) => (
        <span className="tabular-nums font-medium text-foreground">
          {r.query_time_seconds != null ? Number(r.query_time_seconds).toFixed(2) : '—'}
        </span>
      ),
    },
    {
      key: 'rows_examined',
      header: 'Rows examined',
      align: 'right',
      sortable: true,
      sortValue: (r) => r.rows_examined ?? 0,
      render: (r) => (
        <span className="tabular-nums text-muted-foreground">
          {r.rows_examined != null ? formatNumber(r.rows_examined) : '—'}
        </span>
      ),
    },
    {
      key: 'sql_text',
      header: 'Query',
      searchable: true,
      render: (r) => (
        <code className="block max-w-md truncate font-mono text-xs text-foreground" title="Click row to expand">
          {r.sql_text || '—'}
        </code>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="MySQL slow queries"
        description="Inspect queries that exceeded the configured slow-query threshold."
        icon={ListChecks}
      >
        {enabled && (
          <Button
            variant="secondary"
            loading={queriesQuery.isFetching}
            onClick={() => queriesQuery.refetch()}
          >
            <RotateCw className="h-4 w-4" /> Refresh
          </Button>
        )}
        {status && !enabled && (
          <Button onClick={() => setBootstrapOpen(true)}>
            <Zap className="h-4 w-4" /> Enable slow query log
          </Button>
        )}
      </PageHeader>

      {/* Status header card */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Slow query log status</CardTitle>
          {status && (
            <Badge variant={enabled ? 'success' : 'neutral'}>{enabled ? 'Enabled' : 'Disabled'}</Badge>
          )}
        </CardHeader>
        <CardContent>
          {statusQuery.isLoading ? (
            <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
              <Spinner size={16} /> Checking status…
            </div>
          ) : statusQuery.error ? (
            <ErrorState error={statusQuery.error} onRetry={statusQuery.refetch} />
          ) : (
            <>
              <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
                <div className="flex items-start gap-3">
                  <Clock className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                  <div>
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Threshold</div>
                    <div className="mt-0.5 text-sm font-semibold text-foreground tabular-nums">
                      {status?.long_query_time != null ? `${status.long_query_time}s` : '—'}
                    </div>
                  </div>
                </div>
                <div>
                  <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Log output</div>
                  <div className="mt-0.5 text-sm font-semibold text-foreground">{status?.log_output || '—'}</div>
                </div>
                <div>
                  <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Config</div>
                  <div className="mt-0.5">
                    <Badge variant={status?.config_managed ? 'success' : 'warning'}>
                      {status?.config_managed ? 'Managed' : 'Unmanaged'}
                    </Badge>
                  </div>
                </div>
              </div>
              {!enabled && (
                <div className="mt-5 flex flex-col gap-3 rounded-btn border border-border bg-muted/40 p-4 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm text-muted-foreground">
                    The slow query log is disabled. Enabling it configures MariaDB with a 1&nbsp;second threshold and
                    briefly restarts the database.
                  </p>
                  <Button className="shrink-0" onClick={() => setBootstrapOpen(true)}>
                    <Zap className="h-4 w-4" /> Enable slow query log
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {enabled && (
        <>
          {/* Server-side filters */}
          <Card className="mb-6">
            <CardContent>
              <form onSubmit={applyFilters} className="flex flex-wrap items-end gap-4">
                <FormField label="Search query or DB text" className="min-w-[16rem] flex-1">
                  <Input
                    value={draft.q}
                    onChange={(e) => setDraft((d) => ({ ...d, q: e.target.value }))}
                    placeholder="e.g. SELECT * FROM orders"
                  />
                </FormField>
                <FormField label="Exact database" hint="Filter to a single database.">
                  <Input
                    value={draft.db}
                    onChange={(e) => setDraft((d) => ({ ...d, db: e.target.value }))}
                    placeholder="acme_wp"
                  />
                </FormField>
                <FormField label="Limit">
                  <Select
                    value={draft.limit}
                    onChange={(e) => setDraft((d) => ({ ...d, limit: Number(e.target.value) }))}
                  >
                    {LIMIT_OPTIONS.map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </Select>
                </FormField>
                <div className="flex gap-2">
                  <Button type="submit">Search</Button>
                  <Button type="button" variant="secondary" onClick={clearFilters}>Clear</Button>
                </div>
              </form>
            </CardContent>
          </Card>

          <DataTable
            columns={columns}
            data={queriesQuery.data?.queries}
            loading={queriesQuery.isLoading}
            error={queriesQuery.error}
            onRetry={queriesQuery.refetch}
            filterable
            searchPlaceholder="Filter loaded results…"
            pageSize={20}
            initialSort={{ key: 'query_time_seconds', dir: 'desc' }}
            getRowKey={(r, i) => `${r.thread_id ?? ''}-${r.start_time ?? ''}-${i}`}
            onRowClick={(r) => setSelected(r)}
            emptyTitle="No slow queries recorded"
            emptyDescription="No queries have exceeded the slow-query threshold yet, or none match your filters."
            emptyIcon={ListChecks}
          />
        </>
      )}

      {/* Full query detail */}
      <Dialog open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>Slow query detail</DialogTitle>
            <DialogDescription>
              {selected?.start_time ? `Recorded ${selected.start_time}` : 'Query captured in the slow query log.'}
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-4">
            <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
              <Detail label="Database" value={selected?.db || '—'} />
              <Detail label="User / host" value={selected?.user_host || '—'} mono />
              <Detail label="Thread ID" value={selected?.thread_id != null ? String(selected.thread_id) : '—'} mono />
              <Detail
                label="Query time"
                value={selected?.query_time_seconds != null ? `${Number(selected.query_time_seconds).toFixed(2)}s` : '—'}
              />
              <Detail
                label="Lock time"
                value={selected?.lock_time_seconds != null ? `${Number(selected.lock_time_seconds).toFixed(2)}s` : '—'}
              />
              <Detail
                label="Rows examined"
                value={selected?.rows_examined != null ? formatNumber(selected.rows_examined) : '—'}
              />
              <Detail
                label="Rows sent"
                value={selected?.rows_sent != null ? formatNumber(selected.rows_sent) : '—'}
              />
            </div>
            <div>
              <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">SQL</div>
              <pre className="max-h-80 overflow-auto rounded-btn border border-border bg-muted/40 p-3 font-mono text-xs text-foreground whitespace-pre-wrap break-words">
                {selected?.sql_text || '—'}
              </pre>
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setSelected(null)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Enable / bootstrap confirmation */}
      <ConfirmDialog
        open={bootstrapOpen}
        onOpenChange={setBootstrapOpen}
        title="Enable the slow query log?"
        description="This configures MariaDB to log queries slower than 1 second and briefly restarts the database, which may interrupt active connections."
        confirmLabel="Enable slow query log"
        variant="primary"
        loading={bootstrapMut.isPending}
        onConfirm={() => bootstrapMut.mutate()}
      />
    </div>
  )
}

function Detail({ label, value, mono }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={`mt-0.5 text-sm font-medium text-foreground ${mono ? 'font-mono break-all' : ''}`}>{value}</div>
    </div>
  )
}
