import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Download, History, Search, X } from 'lucide-react'
import { get } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Select } from '@/components/ui/Select'
import { Input } from '@/components/ui/Input'

// Badge tone per lifecycle action — statuses, not interactivity.
const ACTION_VARIANT = {
  created: 'success',
  suspended: 'warning',
  unsuspended: 'info',
  terminated: 'danger',
}

const ACTIONS = ['created', 'suspended', 'unsuspended', 'terminated']
const PAGE_SIZE = 50

// Admin-only, read-only lifecycle log. Terminated accounts exist nowhere else
// in the panel — this page (and its CSV export) is their permanent record.
export default function AccountLog() {
  const [searchParams, setSearchParams] = useSearchParams()
  const action = searchParams.get('action') || ''
  const query = searchParams.get('q') || ''
  const page = Math.max(1, Number.parseInt(searchParams.get('page') || '1', 10) || 1)
  const [draftQuery, setDraftQuery] = useState(query)
  useEffect(() => setDraftQuery(query), [query])

  function updateParams(changes) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous)
      Object.entries(changes).forEach(([key, value]) => {
        if (value == null || value === '' || (key === 'page' && value === 1)) next.delete(key)
        else next.set(key, String(value))
      })
      return next
    })
  }

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ['account-events', action, query, page],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) })
      if (action) params.set('action', action)
      if (query) params.set('q', query)
      return get(`/api/v1/audit-log/account-events?${params}`)
    },
    placeholderData: keepPreviousData,
  })

  const entries = data?.entries || []
  const total = data?.total || 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const columns = [
    {
      key: 'created_at', header: 'When', sortable: true, searchable: false,
      render: (r) => <span className="whitespace-nowrap text-muted-foreground">{formatDate(r.created_at)}</span>,
    },
    {
      key: 'action', header: 'Action', sortable: true,
      render: (r) => <Badge variant={ACTION_VARIANT[r.action] || 'neutral'} className="capitalize">{r.action}</Badge>,
    },
    {
      key: 'username', header: 'Account', sortable: true, searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.username}</span>,
    },
    {
      key: 'actor', header: 'Performed by', sortable: true, searchable: true,
      render: (r) => (
        <span className="flex items-center gap-1.5">
          {r.actor}
          <span className="text-xs text-muted-foreground">({r.actor_role})</span>
        </span>
      ),
    },
    {
      key: 'ip', header: 'From IP', searchable: true,
      render: (r) => (r.ip ? <span className="font-mono text-xs">{r.ip}</span> : <span className="text-muted-foreground">—</span>),
    },
    {
      key: 'detail', header: 'Detail', searchable: true,
      render: (r) => (r.detail ? <span className="text-muted-foreground">{r.detail}</span> : '—'),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Account Log"
        description="Every account creation, suspension and termination — when it happened, who did it, and from which IP. Terminated accounts live on only here."
        icon={History}
      >
        <Button asChild variant="secondary">
          <a href={`/api/v1/audit-log/account-events/export.csv${action ? `?action=${action}` : ''}`} download>
            <Download className="h-4 w-4" /> Export CSV
          </a>
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={entries}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        pageSize={0}
        initialSort={{ key: 'created_at', dir: 'desc' }}
        emptyTitle="No account events yet"
        emptyDescription="Entries appear here whenever an account is created, suspended, unsuspended or terminated."
        emptyIcon={History}
        toolbar={
          <form className="flex w-full flex-wrap items-center gap-2" onSubmit={(event) => {
            event.preventDefault()
            updateParams({ q: draftQuery, page: 1 })
          }}>
            <div className="min-w-48 flex-1">
              <Input value={draftQuery} onChange={(event) => setDraftQuery(event.target.value)} placeholder="Search account, actor or IP…" aria-label="Search account log" />
            </div>
            <div className="w-44">
            <Select value={action} onChange={(e) => updateParams({ action: e.target.value, page: 1 })} aria-label="Filter by action">
              <option value="">All actions</option>
              {ACTIONS.map((a) => (
                <option key={a} value={a}>{a[0].toUpperCase() + a.slice(1)}</option>
              ))}
            </Select>
            </div>
            {(query || action) && <Button type="button" variant="ghost" onClick={() => { setDraftQuery(''); setSearchParams(new URLSearchParams()) }}><X className="h-4 w-4" /> Clear</Button>}
            <Button type="submit" loading={isFetching}><Search className="h-4 w-4" /> Search</Button>
          </form>
        }
      />
      {total > 0 && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
          <span>{(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total}</span>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1 || isFetching} onClick={() => updateParams({ page: page - 1 })}>
              <ChevronLeft className="h-4 w-4" /> Previous
            </Button>
            <span>Page {page} / {totalPages}</span>
            <Button variant="outline" size="sm" disabled={page >= totalPages || isFetching} onClick={() => updateParams({ page: page + 1 })}>
              Next <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
