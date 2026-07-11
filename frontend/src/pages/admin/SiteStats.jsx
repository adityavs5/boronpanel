import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BarChart3 } from 'lucide-react'
import { get } from '@/lib/api'
import { formatBytes, titleCase } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Card, CardContent } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'

const PERIODS = ['daily', 'weekly', 'monthly']

// Missing-features batch, goal feature 6: admin server-wide summary,
// alongside (not replacing) each domain's own Site Statistics tab.
export default function SiteStats() {
  const [period, setPeriod] = useState('daily')

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-sitestats', period],
    queryFn: () => get(`/api/v1/admin/sitestats/summary?period=${period}`),
  })

  const rows = data?.domains || []

  const columns = [
    { key: 'domain', header: 'Domain', sortable: true, searchable: true, render: (r) => <span className="font-medium text-foreground">{r.domain}</span> },
    { key: 'pageviews', header: 'Pageviews', align: 'right', sortable: true, sortValue: (r) => r.pageviews, render: (r) => r.pageviews.toLocaleString() },
    { key: 'unique_visitors', header: 'Unique visitors', align: 'right', sortable: true, sortValue: (r) => r.unique_visitors, render: (r) => r.unique_visitors.toLocaleString() },
    { key: 'bytes_served', header: 'Bandwidth', align: 'right', sortable: true, sortValue: (r) => r.bytes_served, render: (r) => formatBytes(r.bytes_served) },
    { key: 'error_404_count', header: '404s', align: 'right', sortable: true, sortValue: (r) => r.error_404_count, render: (r) => r.error_404_count.toLocaleString() },
  ]

  return (
    <div>
      <PageHeader title="Site Statistics" description="Server-wide pageviews and bandwidth, ranked per domain." icon={BarChart3}>
        <FormField label="Period" htmlFor="stats-period" className="w-40">
          <Select id="stats-period" value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map((p) => <option key={p} value={p}>{titleCase(p)}</option>)}
          </Select>
        </FormField>
      </PageHeader>

      <div className="mb-6 grid gap-4 sm:grid-cols-2">
        <Card>
          <CardContent className="py-4">
            <p className="text-2xl font-semibold text-foreground">{(data?.server_total_pageviews ?? 0).toLocaleString()}</p>
            <p className="text-xs text-muted-foreground">Total pageviews</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4">
            <p className="text-2xl font-semibold text-foreground">{formatBytes(data?.server_total_bytes_served ?? 0)}</p>
            <p className="text-xs text-muted-foreground">Total bandwidth served</p>
          </CardContent>
        </Card>
      </div>

      {data && !data.geoip_configured && (
        <Badge variant="outline" className="mb-4">GeoLite2 not configured — top countries unavailable</Badge>
      )}

      <DataTable
        columns={columns}
        data={rows}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search domains…"
        pageSize={20}
        getRowKey={(r) => r.domain}
        initialSort={{ key: 'pageviews', dir: 'desc' }}
        emptyTitle="No site statistics yet"
        emptyDescription="Stats appear here once domains receive real HTTP traffic and the daily snapshot has run."
        emptyIcon={BarChart3}
      />
    </div>
  )
}
