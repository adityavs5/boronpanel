import { useQuery } from '@tanstack/react-query'
import { Boxes, FileCode2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { get } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Button } from '@/components/ui/Button'

export default function SoftwareInventory({ type }) {
  const node = type === 'node'
  const title = node ? 'Node.js Applications' : 'Python Applications'
  const Icon = node ? Boxes : FileCode2
  const query = useQuery({
    queryKey: ['admin-software', type],
    queryFn: () => get(`/api/v1/admin/software/${node ? 'node-apps' : 'python-apps'}`),
  })
  const columns = [
    { key: 'name', header: 'Application', sortable: true, searchable: true, render: row => <span className="font-semibold text-foreground">{row.name}</span> },
    { key: 'username', header: 'Account', sortable: true, searchable: true, render: row => <Button asChild variant="link" size="sm"><Link to={`/accounts/${row.username}`}>{row.username}</Link></Button> },
    { key: 'domain', header: 'Domain', sortable: true, searchable: true },
    { key: 'runtime', header: 'Runtime', render: row => node ? `Node ${row.node_version}` : row.app_type.toUpperCase() },
    { key: 'entry_point', header: 'Entry point', searchable: true, render: row => <code className="text-xs">{row.entry_point}</code> },
    { key: 'port', header: 'Port' },
    { key: 'active', header: 'Status', render: row => <StatusBadge status={row.active ? 'active' : row.enabled ? 'stopped' : 'disabled'} /> },
  ]
  return <div>
    <PageHeader title={title} description={`Server-wide inventory of every ${node ? 'Node.js' : 'Python'} application and its owning account.`} icon={Icon} />
    <DataTable
      data={query.data?.apps || []}
      columns={columns}
      loading={query.isLoading}
      error={query.error}
      onRetry={query.refetch}
      filterable
      pageSize={20}
      searchPlaceholder={`Search ${node ? 'Node.js' : 'Python'} apps…`}
      emptyTitle={`No ${node ? 'Node.js' : 'Python'} applications`}
      emptyDescription="Applications created by hosting accounts will appear here."
      emptyIcon={Icon}
    />
  </div>
}
