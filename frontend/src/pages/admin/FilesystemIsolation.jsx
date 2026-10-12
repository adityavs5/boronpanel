import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Box, CheckCircle2, AlertTriangle, RefreshCw, FlaskConical, CircleDashed, Info, X } from 'lucide-react'
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
  const [dismissedResults, setDismissedResults] = useState(() => new Set())
  const query = useQuery({ queryKey: ['filesystem-isolation'], queryFn: () => get('/api/v1/admin/isolation') })
  const action = useMutation({
    mutationFn: async ({ username, type }) => {
      if (type === 'rebuild') {
        const rebuilt = await post(`/api/v1/admin/isolation/${username}/rebuild`, {})
        return { ...(rebuilt.self_test || rebuilt), rebuilt: true }
      }
      return post(`/api/v1/admin/isolation/${username}/self-test`, {})
    },
    onMutate: value => setRunning(`${value.username}:${value.type}`),
    onSuccess: (result, value) => {
      setTestResults(previous => ({ ...previous, [value.username]: result }))
      setDismissedResults(previous => { const next = new Set(previous); next.delete(value.username); return next })
      if (value.type === 'rebuild') { toast.success('Isolation rebuilt and tested'); setRebuildTarget(null) }
      else if (result.passed) toast.success('Isolation self-test passed')
      else if (result.status === 'incomplete') toast.info('Isolation test is incomplete', 'Idle PHP workers are marked Not verifiable. Open a PHP page and test again for live evidence.')
      else toast.error('Isolation self-test failed', 'See the visible check report for the specific failure.')
      qc.invalidateQueries({ queryKey: ['filesystem-isolation'] })
    },
    onError: (error, value) => {
      setTestResults(previous => ({ ...previous, [value.username]: { request_error: error.message, tested_at: new Date().toISOString(), rebuilt: value.type === 'rebuild' } }))
      toast.error('Isolation action failed', error.message)
    }, onSettled: () => setRunning(null),
  })
  if (query.isLoading) return <CardSkeleton />
  if (query.error) return <ErrorState error={query.error} onRetry={query.refetch} />
  const data = query.data
  const visibleResults = Object.fromEntries(data.accounts
    .filter(row => row.last_test && !dismissedResults.has(row.username))
    .map(row => [row.username, { ...row.last_test, persisted: true }]))
  Object.assign(visibleResults, testResults)
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
    <Card><CardHeader><CardTitle>Account coverage</CardTitle><CardDescription>Web/PHP uses the OLS namespace. Node.js, Python, and Redis units use systemd hardening and the account cgroup. SSH coverage is shown separately.</CardDescription></CardHeader><CardContent><DataTable className="isolation-coverage-table" columns={columns} data={data.accounts} getRowKey={r => r.username} pageSize={25} emptyTitle="No hosting accounts" emptyDescription="Accounts appear here after creation." emptyIcon={Box} /></CardContent></Card>
    {Object.entries(visibleResults).map(([username, result]) => <IsolationResult key={username} username={username} result={result} overview={data} onDismiss={() => { setTestResults(previous => { const next = { ...previous }; delete next[username]; return next }); setDismissedResults(previous => new Set(previous).add(username)) }} />)}
    <ConfirmDialog
      open={!!rebuildTarget}
      onOpenChange={open => { if (!open && !action.isPending) setRebuildTarget(null) }}
      title={`Rebuild isolation for ${rebuildTarget || 'this account'}?`}
      description="Boron will create a fresh isolation environment and gracefully reload the web server configuration. Website files, databases, email, SSL certificates, backups, and PHP sessions are not changed. Active web requests may be interrupted briefly, and temporary files from the old private temporary directory are discarded."
      confirmLabel="Rebuild isolation"
      variant="warning"
      loading={running === `${rebuildTarget}:rebuild`}
      loadingDescription={`Rebuilding and testing isolation for ${rebuildTarget || 'this account'}… this can take up to 30 seconds. Do not close this page.`}
      error={action.error}
      onConfirm={() => action.mutate({ username: rebuildTarget, type: 'rebuild' })}
    />
  </div>
}

function checkState(status, explanation) {
  return { status, explanation }
}

function resultChecks(username, result, overview) {
  if (Array.isArray(result.checks) && result.checks.length) {
    return result.checks.map(check => [check.name, checkState(check.status, check.explanation)])
  }
  if (result.request_error) return [
    ['Private filesystem', checkState('not-verifiable', result.request_error)],
    ['Private temp', checkState('not-verifiable', 'The test did not complete.')],
    ['Process namespace', checkState('not-verifiable', 'The test did not complete.')],
    ['Account environment', checkState('not-verifiable', 'The test did not complete.')],
    ['Resource budget', checkState('not-verifiable', 'The test did not complete.')],
    ['File Manager path', checkState('not-verifiable', 'The test did not complete.')],
  ]
  const idle = result.skipped?.includes('live_php_worker')
  const account = overview.accounts?.find(row => row.username === username)
  const filesystem = idle
    ? checkState('not-verifiable', 'Configured; awaiting a PHP worker. Open a PHP page and test again.')
    : result.own_home_readable === false || result.other_home_hidden === false
      ? checkState('failed', result.own_home_readable === false ? 'The account home was not readable inside its PHP worker.' : 'Another account home was visible inside the PHP worker.')
      : result.own_home_readable === true && result.other_home_hidden === true
        ? checkState('passed', 'The account home is available and other account homes are hidden.')
        : checkState('not-verifiable', 'No peer account was available for a complete visibility check.')
  const tempCapability = overview.capabilities?.private_tmp
  const privateTemp = idle || tempCapability?.status === 'configured'
    ? checkState('not-verifiable', 'Configured; awaiting a PHP worker. Open a PHP page and test again.')
    : tempCapability?.status === 'verified'
      ? checkState('passed', tempCapability.reason)
      : checkState('failed', tempCapability?.reason || 'The PHP worker did not use its private temporary filesystem.')
  const failedWorkers = (result.process_isolation_workers || []).filter(worker => worker.status === 'failed' || worker.private_pid_namespace === false)
  const failedWorkerExplanation = failedWorkers.length
    ? failedWorkers.map(worker => `Worker ${worker.pid}: Failed — shares the host process namespace`).join('. ')
    : 'A live PHP worker shares the host process namespace.'
  const process = result.process_isolation === 'verified'
    ? checkState('passed', 'Live PHP workers use a private process namespace.')
    : result.process_isolation === 'configured_no_live_worker'
      ? checkState('not-verifiable', 'Configured; awaiting a PHP worker. Open a PHP page and test again.')
      : result.process_isolation === 'not_configured'
        ? checkState('failed', 'Private process isolation is not configured for this account.')
        : checkState('failed', result.process_isolation === 'failed' ? failedWorkerExplanation : 'The live PHP worker could not be inspected.')
  const environment = result.namespace_enabled
    ? checkState('passed', 'The account isolation policy is enabled.')
    : checkState('failed', 'The account isolation policy is disabled.')
  const resource = account?.resource?.status === 'verified'
    ? checkState('passed', account.resource.reason)
    : account?.resource?.status === 'degraded'
      ? checkState('failed', account.resource.reason)
      : checkState('not-verifiable', account?.resource?.reason || 'The account resource budget could not be inspected.')
  return [
    ['Private filesystem', filesystem],
    ['Private temp', privateTemp],
    ['Process namespace', process],
    ['Account environment', environment],
    ['Resource budget', resource],
  ]
}

function IsolationResult({ username, result, overview, onDismiss }) {
  const checks = resultChecks(username, result, overview)
  return <Card className="mt-4" aria-live="polite" aria-label={`PHP worker checks for ${username}`}>
    <CardHeader className="flex flex-row items-start justify-between gap-3">
      <div><CardTitle>{result.rebuilt ? 'Rebuild result' : 'Test result'}: {username}</CardTitle><CardDescription>Last tested {result.tested_at ? new Date(result.tested_at).toLocaleString() : 'at an unknown time'}.{result.stale ? ' This result is stale because the account configuration changed.' : ''} Results remain here until dismissed.</CardDescription></div>
      <Button variant="ghost" size="icon-sm" aria-label={`Dismiss isolation result for ${username}`} onClick={onDismiss}><X className="h-4 w-4" /></Button>
    </CardHeader>
    <CardContent className="divide-y divide-border">
      {checks.map(([label, check]) => <div key={label} className="grid gap-1 py-3 sm:grid-cols-[11rem_9rem_1fr] sm:items-start">
        <strong className="text-sm">{label}</strong>
        <Badge className="w-fit" variant={check.status === 'passed' ? 'success' : check.status === 'failed' ? 'danger' : 'info'}>{check.status === 'passed' ? 'Passed' : check.status === 'failed' ? 'Failed' : 'Not verifiable'}</Badge>
        <p className="text-sm text-muted-foreground">{check.explanation}</p>
      </div>)}
    </CardContent>
  </Card>
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
