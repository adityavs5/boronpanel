import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { AlertOctagon, RefreshCw } from 'lucide-react'
import { get } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Table, THead, TBody, TR, TH, TD } from '@/components/ui/Table'
import { TableSkeleton } from '@/components/ui/Skeleton'
import { EmptyState, ErrorState } from '@/components/ui/States'

// Run A feature 7: the last 100 5xx responses from the panel's own API,
// read from /var/log/boron/api-error.log via GET /admin/logs/errors.
export default function ErrorLog() {
  const [params, setParams] = useSearchParams()
  const reference = params.get('reference')
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ['admin-error-log'],
    queryFn: () => get('/api/v1/admin/logs/errors'),
  })

  const errors = (data?.errors || []).filter(error => !reference || error.reference === reference)

  return (
    <div>
      <PageHeader
        title="Error log"
        description="The last 100 server errors (HTTP 5xx) from the panel API, newest first."
        icon={AlertOctagon}
      >
        <Button variant="outline" onClick={() => refetch()} loading={isFetching}>
          <RefreshCw className="h-4 w-4" /> Refresh
        </Button>
      </PageHeader>
      {reference && <div className="mb-4 flex items-center gap-3"><span>Reference: <code>{reference}</code></span><Button variant="outline" size="sm" onClick={() => setParams({})}>Show all errors</Button></div>}

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
                    {e.reference && <div className="font-mono text-xs">Reference: {e.reference}</div>}
                    {e.returncode != null && <div>Command exit status: {e.returncode}</div>}
                    {e.errno != null && <div>OS error number: {e.errno}</div>}
                    {e.frames?.map((frame, index) => <div key={index} className="break-all font-mono text-xs text-muted-foreground">{frame}</div>)}
                    {!e.reference && <span className="text-muted-foreground">Older request record; no diagnostic reference recorded.</span>}
                  </TD>
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
