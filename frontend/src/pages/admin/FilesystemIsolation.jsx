import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Box, CheckCircle2, AlertTriangle, RefreshCw, FlaskConical, CircleDashed, Info } from 'lucide-react'
import { get, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { DataTable } from '@/components/ui/Table'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { toast } from '@/components/ui/Toast'

export default function FilesystemIsolation() {
  const qc = useQueryClient()
  const [running, setRunning] = useState(null)
  const [rebuildTarget, setRebuildTarget] = useState(null)
  const [testResults, setTestResults] = useState({})
  const query = useQuery({ queryKey: ['filesystem-isolation'], queryFn: () => get('/api/v1/admin/isolation') })
  const action = useMutation({
    mutationFn: ({ username, type }) => post(`/api/v1/admin/isolation/${username}/${type}`, {}),
    onMutate: value => setRunning(`${value.username}:${value.type}`),
    onSuccess: (result, value) => {
      if (value.type === 'rebuild') { toast.success('Namespace rebuilt'); setRebuildTarget(null) }
      else { setTestResults(previous => ({ ...previous, [value.username]: result })); if (result.passed) toast.success('Isolation self-test passed'); else if (result.status === 'incomplete') toast.info('Isolation test is incomplete', 'See the checks below. Idle PHP workers or a missing peer account can leave checks untested.'); else toast.error('Isolation self-test failed', 'See the failed checks below; runtime coverage is measured separately.') }
      qc.invalidateQueries({ queryKey: ['filesystem-isolation'] })
    },
    onError: e => toast.error('Isolation action failed', e.message), onSettled: () => setRunning(null),
  })
  if (query.isLoading) return <CardSkeleton />
  if (query.error) return <ErrorState error={query.error} onRetry={query.refetch} />
  const data = query.data
  const columns = [
    { key: 'username', header: 'Account', render: r => <strong>{r.username}</strong> },
    { key: 'web', header: 'Web / PHP', render: r => <State value={r.web} /> },
    { key: 'terminal', header: 'Terminal', render: r => <State value={r.terminal} /> },
    { key: 'services', header: 'App services', render: r => <State value={r.services} /> },
    { key: 'file_manager', header: 'File Manager', render: r => <State value={r.file_manager} /> },
    { key: 'ssh_sftp', header: 'SSH / SFTP', render: r => <State value={r.ssh_sftp} /> },
    { key: 'resource', header: 'Resource budget', render: r => <State value={r.resource} /> },
    { key: 'actions', header: '', align: 'right', render: r => <div className="flex justify-end gap-2"><Button variant="ghost" size="sm" loading={running === `${r.username}:self-test`} onClick={() => action.mutate({ username: r.username, type: 'self-test' })}><FlaskConical className="h-4 w-4" />Test</Button><Button variant="secondary" size="sm" loading={running === `${r.username}:rebuild`} onClick={() => setRebuildTarget(r.username)}><RefreshCw className="h-4 w-4" />Rebuild</Button></div> },
  ]
  return <div>
    <PageHeader title="Filesystem Isolation" description="OpenLiteSpeed mount namespaces and hardened application services by account." icon={Box} />
    <Card className="mb-5 border-info/40 bg-info/10"><CardContent className="flex gap-3 py-4" role="status"><Info className="h-6 w-6 shrink-0 text-info" /><div><p className="font-medium">About isolation status</p><p className="text-sm text-muted-foreground">{data.warning}</p></div></CardContent></Card>
    <div className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Capability label="Private account filesystem" value={data.capabilities.mount_namespace} />
      <Capability label="Private temporary files" value={data.capabilities.private_tmp} />
      <Capability label="Aggregate resource limits" value={data.capabilities.resource_enforcement} />
      <Capability label="Private process namespace" value={data.capabilities.pid_namespace} />
    </div>
    <Card><CardHeader><CardTitle>Account coverage</CardTitle><CardDescription>Web/PHP uses the OLS namespace. Node.js, Python, and Redis units use systemd hardening and the account cgroup. SSH coverage is shown separately.</CardDescription></CardHeader><CardContent><DataTable columns={columns} data={data.accounts} getRowKey={r => r.username} pageSize={25} emptyTitle="No hosting accounts" emptyDescription="Accounts appear here after creation." emptyIcon={Box} /></CardContent></Card>
    {Object.entries(testResults).map(([username, result]) => <Card key={username} className="mt-4"><CardHeader><CardTitle>Self-test: {username}</CardTitle><CardDescription>This canary result is separate from the live workload observations above.</CardDescription></CardHeader><CardContent className="space-y-2"><Badge variant={result.status === 'passed' ? 'success' : result.status === 'incomplete' ? 'info' : 'danger'}>{result.status}</Badge><p>Account environment: {result.namespace_enabled ? 'Enabled' : 'Not enabled'}</p><p>Own home access: {result.own_home_readable == null ? 'Not tested — open a PHP page and retry' : result.own_home_readable ? 'Passed' : 'Failed'}</p><p>Other account visibility: {result.other_home_hidden == null ? (result.skipped?.includes('live_php_worker') ? 'Not tested — open a PHP page and retry' : 'Not tested — no peer account') : result.other_home_hidden ? 'Passed — hidden' : 'Failed — visible'}</p><p>Web/PHP process isolation: {result.process_isolation?.replaceAll('_', ' ')}</p>{result.skipped?.length > 0 && <p className="text-muted-foreground">Untested checks: {result.skipped.map(check => check.replaceAll('_', ' ')).join(', ')}</p>}</CardContent></Card>)}
    <ConfirmDialog
      open={!!rebuildTarget}
      onOpenChange={open => { if (!open && !action.isPending) setRebuildTarget(null) }}
      title={`Rebuild isolation for ${rebuildTarget || 'this account'}?`}
      description="Boron will create a fresh isolation environment and gracefully reload the web server configuration. Website files, databases, email, SSL certificates, backups, and PHP sessions are not changed. Active web requests may be interrupted briefly, and temporary files from the old private temporary directory are discarded."
      confirmLabel="Rebuild isolation"
      variant="warning"
      loading={running === `${rebuildTarget}:rebuild`}
      onConfirm={() => action.mutate({ username: rebuildTarget, type: 'rebuild' })}
    />
  </div>
}

function Capability({ label, value }) {
  const status = value?.status || 'unavailable'
  const Icon = status === 'verified' ? CheckCircle2 : status === 'configured' ? CircleDashed : AlertTriangle
  const tone = status === 'verified' ? 'text-success' : status === 'degraded' ? 'text-destructive' : 'text-warning'
  return <Card><CardContent className="flex items-start gap-3 py-4"><Icon className={`mt-0.5 h-6 w-6 shrink-0 ${tone}`} /><div><p className="font-medium">{label}</p><p className="text-xs capitalize text-muted-foreground">{status.replaceAll('_', ' ')}</p><p className="mt-1 text-xs text-muted-foreground">{value?.reason}</p></div></CardContent></Card>
}
function State({ value }) {
  const status = typeof value === 'string' ? value : value?.status || 'unavailable'
  const variant = status === 'verified' ? 'success' : status === 'degraded' ? 'danger' : 'outline'
  return <span title={typeof value === 'object' ? value?.reason : ''}><Badge variant={variant}>{status.replaceAll('_', ' ')}</Badge></span>
}
