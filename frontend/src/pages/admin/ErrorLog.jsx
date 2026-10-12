import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { AlertOctagon, RefreshCw, Copy } from 'lucide-react'
import { get } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Table, THead, TBody, TR, TH, TD } from '@/components/ui/Table'
import { TableSkeleton } from '@/components/ui/Skeleton'
import { EmptyState, ErrorState } from '@/components/ui/States'

// Run A feature 7: the last 100 5xx responses from the panel's own API,
// read from /var/log/boron/api-error.log via GET /admin/logs/errors.
export default function ErrorLog() {
  const [params, setParams] = useSearchParams()
  const query = params.toString()
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ['admin-error-log', query],
    queryFn: () => get(`/api/v1/admin/logs/errors${query ? `?${query}` : ''}`),
  })

  const errors = data?.errors || []

  function setFilter(name, value) {
    setParams(previous => {
      const next = new URLSearchParams(previous)
      if (value) next.set(name, value)
      else next.delete(name)
      return next
    }, { replace: true })
  }

  return (
    <div>
      <PageHeader
        title="Error log"
        description="Search correlated panel API errors by reference, request, date, or status."
        icon={AlertOctagon}
      >
        <Button variant="outline" onClick={() => refetch()} loading={isFetching}>
          <RefreshCw className="h-4 w-4" /> Refresh
        </Button>
      </PageHeader>
      <div className="mb-4 grid gap-3 border border-border bg-card p-4 sm:grid-cols-2 xl:grid-cols-5">
        <Input aria-label="Error reference" placeholder="Error reference" value={params.get('reference') || ''} onChange={e => setFilter('reference', e.target.value.trim())} />
        <Input aria-label="Request ID" placeholder="Request ID" value={params.get('request_id') || ''} onChange={e => setFilter('request_id', e.target.value.trim())} />
        <Input aria-label="From date" type="datetime-local" value={params.get('date_from') || ''} onChange={e => setFilter('date_from', e.target.value)} />
        <Input aria-label="To date" type="datetime-local" value={params.get('date_to') || ''} onChange={e => setFilter('date_to', e.target.value)} />
        <Select aria-label="HTTP status" value={params.get('status') || ''} onChange={e => setFilter('status', e.target.value)}>
          <option value="">All errors</option>
          <option value="400">400</option><option value="401">401</option><option value="403">403</option>
          <option value="404">404</option><option value="422">422</option><option value="429">429</option>
          <option value="500">500</option><option value="502">502</option><option value="503">503</option>
        </Select>
        {query && <Button variant="ghost" size="sm" onClick={() => setParams({})}>Clear filters</Button>}
      </div>

      <div className="rounded-card border border-border bg-card overflow-hidden">
        {isLoading ? (
          <TableSkeleton rows={8} cols={6} />
        ) : error ? (
          <ErrorState error={error} onRetry={refetch} />
        ) : errors.length === 0 ? (
          <EmptyState
            icon={AlertOctagon}
            title="No recent errors"
            description="No 5xx responses have been logged. That's a good sign."
          />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Time</TH>
                <TH>Status</TH>
                <TH>Method</TH>
                <TH>Path</TH>
                <TH>Details</TH>
                <TH>Request ID</TH>
                <TH>User</TH>
                <TH>IP</TH>
                <TH>Duration</TH>
              </tr>
            </THead>
            <TBody>
              {errors.map((e, i) => (
                <TR key={`${e.timestamp}-${i}`}>
                  <TD className="whitespace-nowrap text-muted-foreground tabular-nums">
                    {formatDate(e.timestamp)}
                  </TD>
                  <TD>
                    <span className="inline-flex items-center rounded-btn bg-danger/10 px-2 py-0.5 font-mono text-xs font-semibold text-danger">
                      {e.status}
                    </span>
                  </TD>
                  <TD className="whitespace-nowrap font-mono text-xs">{e.method}</TD>
                  <TD className="max-w-md">
                    <span className="block truncate font-mono text-xs" title={e.path}>
                      {e.path}
                    </span>
                  </TD>
                  <TD className="min-w-64 max-w-lg whitespace-normal">
                    {e.operation && <div className="font-medium">{e.operation} · {e.error_type}</div>}
                    {e.reference && <div className="flex items-center gap-1 font-mono text-xs">Reference: {e.reference}<button type="button" aria-label="Copy error reference" onClick={() => navigator.clipboard?.writeText(e.reference)}><Copy className="h-3.5 w-3.5" /></button></div>}
                    {e.returncode != null && <div>Command exit status: {e.returncode}</div>}
                    {e.errno != null && <div>OS error number: {e.errno}</div>}
                    {e.frames?.map((frame, index) => <div key={index} className="break-all font-mono text-xs text-muted-foreground">{frame}</div>)}
                    {!e.reference && <span className="text-muted-foreground">Older request record; no diagnostic reference recorded.</span>}
                  </TD>
                  <TD className="max-w-48"><code className="block truncate text-xs" title={e.request_id || ''}>{e.request_id || '—'}</code></TD>
                  <TD className="text-muted-foreground">{e.user || '—'}</TD>
                  <TD className="whitespace-nowrap font-mono text-xs text-muted-foreground">
                    {e.ip || '—'}
                  </TD>
                  <TD className="whitespace-nowrap tabular-nums text-muted-foreground">
                    {e.duration_ms != null ? `${e.duration_ms} ms` : '—'}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </div>
    </div>
  )
}
