import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { ScrollText, Download, Search, X, ChevronLeft, ChevronRight } from 'lucide-react'
import { get } from '@/lib/api'
import { formatDate, truncate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Table, THead, TBody, TR, TH, TD } from '@/components/ui/Table'
import { TableSkeleton } from '@/components/ui/Skeleton'
import { EmptyState, ErrorState } from '@/components/ui/States'

const PAGE_SIZE = 50
const EMPTY_FILTERS = { q: '', actor: '', op: '', result: '' }

// Build a query string from the applied filters, optionally including paging.
function buildParams(filters, page, withPaging) {
  const p = new URLSearchParams()
  Object.entries(filters).forEach(([k, v]) => {
    if (v) p.set(k, v)
  })
  if (withPaging) {
    p.set('page', String(page))
    p.set('page_size', String(PAGE_SIZE))
  }
  return p.toString()
}

export default function AuditLog() {
  const [searchParams, setSearchParams] = useSearchParams()
  const applied = Object.fromEntries(Object.keys(EMPTY_FILTERS).map((key) => [key, searchParams.get(key) || '']))
  const page = Math.max(1, Number.parseInt(searchParams.get('page') || '1', 10) || 1)
  // `draft` holds the in-progress form values; `applied` is what drives the
  // query. Filters commit on submit (Search) so we don't refetch per keystroke.
  const [draft, setDraft] = useState(applied)
  const appliedKey = searchParams.toString()
  useEffect(() => setDraft(applied), [appliedKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ['audit-log', applied, page],
    queryFn: () => get(`/api/v1/audit-log?${buildParams(applied, page, true)}`),
    placeholderData: keepPreviousData,
  })

  const entries = data?.entries || []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const rangeStart = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const rangeEnd = Math.min(page * PAGE_SIZE, total)

  const setField = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }))

  function applyFilters(e) {
    e?.preventDefault()
    setSearchParams(() => {
      const next = new URLSearchParams()
      Object.entries(draft).forEach(([key, value]) => { if (value) next.set(key, value) })
      return next
    })
  }

  function clearFilters() {
    setDraft(EMPTY_FILTERS)
    setSearchParams(new URLSearchParams())
  }

  function setPage(nextPage) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous)
      if (nextPage <= 1) next.delete('page')
      else next.set('page', String(nextPage))
      return next
    })
  }

  function exportCsv() {
    window.open(`/api/v1/audit-log/export.csv?${buildParams(applied, page, false)}`)
  }

  return (
    <div>
      <PageHeader
        title="Audit log"
        description="Every privileged action performed on this server, with actor, target, and result."
        icon={ScrollText}
      >
        <Button variant="outline" onClick={exportCsv}>
          <Download className="h-4 w-4" /> Export CSV
        </Button>
      </PageHeader>

      <Card className="mb-6">
        <CardContent className="py-4">
          <form onSubmit={applyFilters}>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <FormField label="Search" htmlFor="audit-q" className="lg:col-span-2">
                <Input
                  id="audit-q"
                  value={draft.q}
                  onChange={setField('q')}
                  placeholder="Search all fields…"
                />
              </FormField>
              <FormField label="Actor" htmlFor="audit-actor">
                <Input
                  id="audit-actor"
                  value={draft.actor}
                  onChange={setField('actor')}
                  placeholder="e.g. admin"
                />
              </FormField>
              <FormField label="Operation" htmlFor="audit-op">
                <Input
                  id="audit-op"
                  value={draft.op}
                  onChange={setField('op')}
                  placeholder="e.g. account.suspend"
                />
              </FormField>
              <FormField label="Result" htmlFor="audit-result">
                <Select id="audit-result" value={draft.result} onChange={setField('result')}>
                  <option value="">All results</option>
                  <option value="ok">Ok</option>
                  <option value="error">Error</option>
                </Select>
              </FormField>
            </div>
            <div className="mt-4 flex items-center justify-end gap-2">
              <Button type="button" variant="ghost" onClick={clearFilters}>
                <X className="h-4 w-4" /> Clear
              </Button>
              <Button type="submit" loading={isFetching}>
                <Search className="h-4 w-4" /> Search
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <div className="rounded-card border border-border bg-card overflow-hidden">
        {isLoading ? (
          <TableSkeleton rows={10} cols={6} />
        ) : error ? (
          <ErrorState error={error} onRetry={refetch} />
        ) : entries.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title="No audit entries"
            description="No log entries match your current filters."
          />
        ) : (
          <>
          <div className="divide-y divide-border sm:hidden">
            {entries.map((entry) => (
              <article key={entry.id} className="space-y-2.5 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate font-mono text-sm font-medium text-foreground">{entry.op}</div>
                    <div className="text-xs text-muted-foreground">{formatDate(entry.created_at)}</div>
                  </div>
                  <StatusBadge status={entry.result} />
                </div>
                <dl className="grid grid-cols-[4.5rem_1fr] gap-x-3 gap-y-1 text-sm">
                  <dt className="text-muted-foreground">Actor</dt>
                  <dd className="min-w-0 break-words text-foreground">{entry.actor || '—'}{entry.role ? ` (${entry.role})` : ''}</dd>
                  <dt className="text-muted-foreground">Target</dt>
                  <dd className="min-w-0 break-words text-foreground">{entry.target || '—'}</dd>
                  <dt className="text-muted-foreground">Detail</dt>
                  <dd className="min-w-0 break-words text-foreground">{entry.detail || '—'}</dd>
                </dl>
              </article>
            ))}
          </div>
          <div className="hidden sm:block">
          <Table>
            <THead>
              <tr>
                <TH>Time</TH>
                <TH>Actor</TH>
                <TH>Operation</TH>
                <TH>Target</TH>
                <TH>Result</TH>
                <TH>Detail</TH>
              </tr>
            </THead>
            <TBody>
              {entries.map((e) => (
                <TR key={e.id}>
                  <TD className="whitespace-nowrap text-muted-foreground tabular-nums">
                    {formatDate(e.created_at)}
                  </TD>
                  <TD>
                    <div className="font-medium text-foreground">{e.actor || '—'}</div>
                    {e.role && <div className="text-xs text-muted-foreground">{e.role}</div>}
                  </TD>
                  <TD className="whitespace-nowrap font-mono text-xs">{e.op}</TD>
                  <TD className="text-muted-foreground">{e.target || '—'}</TD>
                  <TD>
                    <StatusBadge status={e.result} />
                  </TD>
                  <TD className="max-w-xs">
                    <span className="block truncate text-muted-foreground" title={e.detail || ''}>
                      {e.detail ? truncate(e.detail, 80) : '—'}
                    </span>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
          </div>
          </>
        )}

        {!isLoading && !error && total > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3 text-sm text-muted-foreground">
            <span className="tabular-nums">
              {rangeStart}–{rangeEnd} of {total}
            </span>
            <div className="flex items-center gap-1 sm:gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage(Math.max(1, page - 1))}
                disabled={page <= 1 || isFetching}
              >
                <ChevronLeft className="h-4 w-4" /> Previous
              </Button>
              <span className="px-1 tabular-nums">
                Page {page} / {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                disabled={page >= totalPages || isFetching}
              >
                Next <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
