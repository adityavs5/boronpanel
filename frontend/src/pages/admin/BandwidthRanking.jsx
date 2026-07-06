import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Gauge, Trophy } from 'lucide-react'
import { get } from '@/lib/api'
import { formatBytes, titleCase } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { ProgressBar } from '@/components/ui/Progress'

const PERIODS = ['daily', 'weekly', 'monthly']

export default function BandwidthRanking() {
  const navigate = useNavigate()
  const [period, setPeriod] = useState('monthly')

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-bandwidth-ranking', period],
    queryFn: () => get(`/api/v1/admin/bandwidth/ranking?period=${period}`),
  })

  // Normalize (the API may key the account as `username` or `account`, and the
  // metric as `bytes_served` or `total`), then rank by bytes served descending.
  const rows = (data?.ranking || [])
    .map((e) => ({
      username: e.username || e.account || '—',
      bytes: e.bytes_served ?? e.total ?? 0,
    }))
    .sort((a, b) => b.bytes - a.bytes)
    .map((e, i) => ({ ...e, rank: i + 1 }))

  const maxBytes = rows[0]?.bytes || 0
  const top5 = rows.slice(0, 5)

  const columns = [
    {
      key: 'rank',
      header: '#',
      sortable: true,
      sortValue: (r) => r.rank,
      cellClassName: 'w-12',
      render: (r) => <span className="tabular-nums text-muted-foreground">{r.rank}</span>,
    },
    {
      key: 'username',
      header: 'Account',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.username}</span>,
    },
    {
      key: 'bytes',
      header: 'Bytes served',
      align: 'right',
      sortable: true,
      sortValue: (r) => r.bytes,
      render: (r) => <span className="tabular-nums">{formatBytes(r.bytes)}</span>,
    },
  ]

  return (
    <div>
      <PageHeader
        title="Bandwidth ranking"
        description="Accounts ranked by bandwidth served across the server."
        icon={Gauge}
      >
        <FormField label="Period" htmlFor="bw-period" className="w-40">
          <Select id="bw-period" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map((p) => (
              <option key={p} value={p}>{titleCase(p)}</option>
            ))}
          </Select>
        </FormField>
      </PageHeader>

      {top5.length > 0 && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Trophy className="h-4 w-4 text-accent-600" />
              Top {top5.length} accounts
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {top5.map((r) => (
              <div key={r.username} className="space-y-1.5">
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2 font-medium text-foreground">
                    <span className="tabular-nums text-muted-foreground">#{r.rank}</span>
                    {r.username}
                  </span>
                  <span className="font-medium tabular-nums text-foreground">{formatBytes(r.bytes)}</span>
                </div>
                <ProgressBar value={r.bytes} max={maxBytes || 1} />
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <DataTable
        columns={columns}
        data={rows}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search accounts…"
        pageSize={20}
        getRowKey={(r) => r.username}
        onRowClick={(r) => navigate(`/accounts/${r.username}`)}
        initialSort={{ key: 'bytes', dir: 'desc' }}
        emptyTitle="No bandwidth data collected yet"
        emptyDescription="Bandwidth usage will appear here once accounts start serving traffic."
        emptyIcon={Gauge}
      />
    </div>
  )
}
