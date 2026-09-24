import { useQuery } from '@tanstack/react-query'
import { Mail, RefreshCw } from 'lucide-react'
import { get } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'

const statusTone = { sent: 'success', bounced: 'danger', deferred: 'warning', rejected: 'danger' }

export default function MailTracking() {
  const query = useQuery({ queryKey: ['admin-mail-tracking'], queryFn: () => get('/api/v1/admin/mail/tracking'), staleTime: 60_000 })
  const data = query.data || { summary: {}, by_account: [], by_sender: [], by_domain: [], scripts: [], entries: [] }
  return <div>
    <PageHeader title="Email Tracking" description="On-demand Postfix delivery and PHP mail activity. Data is parsed only when this page is opened, so monitoring adds no continuous database load." icon={Mail}>
      <Button variant="secondary" loading={query.isFetching} onClick={() => query.refetch()}><RefreshCw className="h-4 w-4" />Refresh</Button>
    </PageHeader>
    <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{[
      ['Recent events', data.summary.total || 0], ['Sent', data.summary.sent || 0], ['Bounced', data.summary.bounced || 0], ['Deferred', data.summary.deferred || 0], ['Rejected', data.summary.rejected || 0],
    ].map(([label, value]) => <Card key={label}><CardContent className="p-4"><div className="text-2xl font-semibold text-foreground">{value}</div><div className="text-sm text-muted-foreground">{label}</div></CardContent></Card>)}</div>
    <Tabs defaultValue="events"><TabsList><TabsTrigger value="events">Delivery events</TabsTrigger><TabsTrigger value="accounts">Accounts</TabsTrigger><TabsTrigger value="senders">Senders & domains</TabsTrigger><TabsTrigger value="scripts">PHP scripts</TabsTrigger></TabsList>
      <TabsContent value="events"><DataTable loading={query.isLoading} error={query.error} onRetry={query.refetch} data={data.entries} filterable pageSize={25} columns={[
        { key: 'timestamp', header: 'Time', render: row => <span className="font-mono text-xs whitespace-nowrap">{row.timestamp}</span> },
        { key: 'account', header: 'Account', searchable: true, render: row => row.account || 'External' },
        { key: 'from', header: 'From', searchable: true, render: row => <span className="font-mono text-xs break-all">{row.from || '—'}</span> },
        { key: 'to', header: 'To', searchable: true, render: row => <span className="font-mono text-xs break-all">{row.to}</span> },
        { key: 'status', header: 'Status', render: row => <Badge variant={statusTone[row.status] || 'neutral'}>{row.status}</Badge> },
        { key: 'source_user', header: 'Origin', render: row => row.source_user ? `Local user: ${row.source_user}` : 'SMTP' },
        { key: 'reason', header: 'Result', render: row => <span className="text-xs text-muted-foreground break-all">{row.reason}</span> },
      ]} emptyTitle="No recent mail activity" emptyDescription="Postfix delivery activity will appear here." emptyIcon={Mail} /></TabsContent>
      <TabsContent value="accounts"><DataTable data={data.by_account} columns={[{ key: 'account', header: 'Account', sortable: true }, { key: 'count', header: 'Messages', sortable: true }]} emptyTitle="No account activity" /></TabsContent>
      <TabsContent value="senders"><div className="grid gap-5 lg:grid-cols-2"><DataTable data={data.by_sender} columns={[{ key: 'sender', header: 'Sender', searchable: true }, { key: 'count', header: 'Messages', sortable: true }]} filterable emptyTitle="No senders" /><DataTable data={data.by_domain} columns={[{ key: 'domain', header: 'Domain', searchable: true }, { key: 'count', header: 'Messages', sortable: true }]} filterable emptyTitle="No domains" /></div></TabsContent>
      <TabsContent value="scripts"><DataTable data={data.scripts} filterable pageSize={25} columns={[{ key: 'account', header: 'Account', searchable: true }, { key: 'script', header: 'Script', searchable: true, render: row => <code className="text-xs break-all">{row.script}</code> }, { key: 'to', header: 'Recipient', searchable: true }, { key: 'count', header: 'Calls', sortable: true }]} emptyTitle="No PHP mail calls" emptyDescription="PHP mail() activity appears after the updated vhost configuration is applied." /></TabsContent>
    </Tabs>
  </div>
}
