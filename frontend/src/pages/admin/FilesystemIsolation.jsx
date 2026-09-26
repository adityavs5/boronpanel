import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Box, CheckCircle2, AlertTriangle, RefreshCw, FlaskConical } from 'lucide-react'
import { get, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { DataTable } from '@/components/ui/Table'
import { ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { toast } from '@/components/ui/Toast'

export default function FilesystemIsolation() {
  const qc = useQueryClient()
  const [running, setRunning] = useState(null)
  const query = useQuery({ queryKey: ['filesystem-isolation'], queryFn: () => get('/api/v1/admin/isolation') })
  const action = useMutation({
    mutationFn: ({ username, type }) => post(`/api/v1/admin/isolation/${username}/${type}`, {}),
    onMutate: value => setRunning(`${value.username}:${value.type}`),
    onSuccess: (result, value) => { toast.success(value.type === 'rebuild' ? 'Namespace rebuilt' : result.passed ? 'Isolation self-test passed' : 'Isolation self-test found a problem'); qc.invalidateQueries({ queryKey: ['filesystem-isolation'] }) },
    onError: e => toast.error('Isolation action failed', e.message), onSettled: () => setRunning(null),
  })
  if (query.isLoading) return <CardSkeleton />
  if (query.error) return <ErrorState error={query.error} onRetry={query.refetch} />
  const data = query.data
  const columns = [
    { key: 'username', header: 'Account', render: r => <strong>{r.username}</strong> },
    { key: 'web', header: 'Web / PHP', render: r => <State value={r.web} /> },
    { key: 'terminal', header: 'Terminal', render: r => <State value={r.terminal} /> },
    { key: 'services', header: 'App services', render: r => r.services.length ? <span>{r.services.join(', ')} <Badge variant="outline">hardened</Badge></span> : '—' },
    { key: 'ssh_sftp', header: 'SSH / SFTP', render: r => <State value={r.ssh_sftp} /> },
    { key: 'actions', header: '', align: 'right', render: r => <div className="flex justify-end gap-2"><Button variant="ghost" size="sm" loading={running === `${r.username}:self-test`} onClick={() => action.mutate({ username: r.username, type: 'self-test' })}><FlaskConical className="h-4 w-4" />Test</Button><Button variant="secondary" size="sm" loading={running === `${r.username}:rebuild`} onClick={() => action.mutate({ username: r.username, type: 'rebuild' })}><RefreshCw className="h-4 w-4" />Rebuild</Button></div> },
  ]
  return <div>
    <PageHeader title="Filesystem Isolation" description="OpenLiteSpeed mount namespaces and hardened application services by account." icon={Box} />
    <Card className="mb-5 border-warning/40 bg-warning/5"><CardContent className="flex gap-3 py-4"><AlertTriangle className="h-6 w-6 shrink-0 text-warning" /><div><p className="font-medium">Mount isolation boundary</p><p className="text-sm text-muted-foreground">{data.warning}</p></div></CardContent></Card>
    <div className="mb-5 grid gap-4 sm:grid-cols-3">
      <Capability label="Private account filesystem" enabled={data.capabilities.mount_namespace} />
      <Capability label="Private temporary files" enabled={data.capabilities.private_tmp} />
      <Capability label="Private process namespace" enabled={data.capabilities.pid_namespace} />
    </div>
    <Card><CardHeader><CardTitle>Account coverage</CardTitle><CardDescription>Web/PHP uses the OLS namespace. Node.js, Python, and Redis units use systemd hardening and the account cgroup. SSH coverage is shown separately.</CardDescription></CardHeader><CardContent><DataTable columns={columns} data={data.accounts} getRowKey={r => r.username} pageSize={25} emptyTitle="No hosting accounts" emptyDescription="Accounts appear here after creation." emptyIcon={Box} /></CardContent></Card>
  </div>
}

function Capability({ label, enabled }) { return <Card><CardContent className="flex items-center gap-3 py-4">{enabled ? <CheckCircle2 className="h-6 w-6 text-success" /> : <AlertTriangle className="h-6 w-6 text-warning" />}<div><p className="font-medium">{label}</p><p className="text-xs text-muted-foreground">{enabled ? 'Active' : 'Not provided by this layer'}</p></div></CardContent></Card> }
function State({ value }) { const good = value === 'isolated'; return <Badge variant={good ? 'success' : 'outline'}>{String(value).replaceAll('_', ' ')}</Badge> }
