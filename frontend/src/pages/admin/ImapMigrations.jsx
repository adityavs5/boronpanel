import { useQuery } from '@tanstack/react-query'
import { ArrowRightLeft } from 'lucide-react'
import { get } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Badge } from '@/components/ui/Badge'
import { StatusBadge } from '@/components/ui/StatusBadge'

// Missing-features batch, goal feature 1: admin sees every active IMAPSync
// migration job across every account (source credentials never travel over
// this endpoint -- ImapMigrationJob has no password column at all).
export default function ImapMigrations() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-imap-migrations'],
    queryFn: () => get('/api/v1/admin/imap-migrations'),
    refetchInterval: (query) => ((query.state.data?.jobs || []).length ? 5000 : false),
  })

  const columns = [
    { key: 'username', header: 'Account', render: (r) => <Badge variant="outline">{r.username || '—'}</Badge> },
    { key: 'mailbox', header: 'Destination mailbox', render: (r) => <span className="font-mono text-xs">{r.mailbox}</span> },
    { key: 'source_host', header: 'Source host', render: (r) => <span className="font-mono text-xs">{r.source_host}</span> },
    { key: 'status', header: 'Status', render: (r) => <StatusBadge status={r.status} /> },
    { key: 'progress', header: 'Progress', render: (r) => `${r.folders_done}/${r.folders_total} folders` },
    { key: 'progress_message', header: 'Detail', render: (r) => <span className="text-xs text-muted-foreground">{r.progress_message || '—'}</span> },
  ]

  return (
    <div>
      <PageHeader title="IMAP Migrations" description="Every active customer-initiated email migration, across every account." icon={ArrowRightLeft} />
      <DataTable
        columns={columns}
        data={data?.jobs || []}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        pageSize={20}
        getRowKey={(r) => r.id}
        emptyTitle="No active migrations"
        emptyDescription="Nothing is currently syncing from an external IMAP server."
        emptyIcon={ArrowRightLeft}
      />
    </div>
  )
}
