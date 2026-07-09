import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { History, Download } from 'lucide-react'
import { get } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Select } from '@/components/ui/Select'

// Badge tone per lifecycle action — statuses, not interactivity.
const ACTION_VARIANT = {
  created: 'success',
  suspended: 'warning',
  unsuspended: 'info',
  terminated: 'danger',
}

const ACTIONS = ['created', 'suspended', 'unsuspended', 'terminated']

// Admin-only, read-only lifecycle log. Terminated accounts exist nowhere else
// in the panel — this page (and its CSV export) is their permanent record.
export default function AccountLog() {
  const [action, setAction] = useState('')

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['account-events', action],
    queryFn: () => get(`/api/v1/audit-log/account-events?page_size=500${action ? `&action=${action}` : ''}`),
  })

  const entries = data?.entries || []

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
        filterable
        searchPlaceholder="Search account, actor or IP…"
        pageSize={20}
        initialSort={{ key: 'created_at', dir: 'desc' }}
        emptyTitle="No account events yet"
        emptyDescription="Entries appear here whenever an account is created, suspended, unsuspended or terminated."
        emptyIcon={History}
        toolbar={
          <div className="w-44">
            <Select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Filter by action">
              <option value="">All actions</option>
              {ACTIONS.map((a) => (
                <option key={a} value={a}>{a[0].toUpperCase() + a.slice(1)}</option>
              ))}
            </Select>
          </div>
        }
      />
    </div>
  )
}
