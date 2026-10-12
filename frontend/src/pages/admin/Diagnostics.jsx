import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, CircleHelp, Download, RefreshCw, Stethoscope, XCircle } from 'lucide-react'
import { api, get, post } from '@/lib/api'
import { formatBytes, formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Checkbox } from '@/components/ui/Toggle'
import { ErrorState, EmptyState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'

const statusMeta = {
  pass: { label: 'Passed', variant: 'success', icon: CheckCircle2 },
  warn: { label: 'Warning', variant: 'warning', icon: AlertTriangle },
  fail: { label: 'Failed', variant: 'danger', icon: XCircle },
  unknown: { label: 'Unknown', variant: 'neutral', icon: CircleHelp },
}

export default function Diagnostics() {
  const queryClient = useQueryClient()
  const [result, setResult] = useState(null)
  const [includeNames, setIncludeNames] = useState(false)
  const jobs = useQuery({
    queryKey: ['support-bundles'],
    queryFn: () => get('/api/v1/admin/support-bundles'),
    refetchInterval: (query) => query.state.data?.jobs?.some((job) => ['queued', 'running'].includes(job.status)) ? 2000 : false,
  })
  const run = useMutation({
    mutationFn: () => post('/api/v1/admin/diagnostics/run', {}),
    onSuccess: (data) => { setResult(data); toast.success('Diagnostics completed') },
    onError: (error) => toast.error('Diagnostics could not complete', error.message),
  })
  const createBundle = useMutation({
    mutationFn: () => post('/api/v1/admin/support-bundles', { include_names: includeNames }),
    onSuccess: () => {
      toast.success('Support bundle queued', 'It will be available here for one secure download.')
      queryClient.invalidateQueries({ queryKey: ['support-bundles'] })
    },
    onError: (error) => toast.error('Support bundle could not be created', error.message),
  })
  const downloadBundle = useMutation({
    mutationFn: async (job) => {
      const response = await api.post(`/api/v1/admin/support-bundles/${job.id}/download`, {}, { responseType: 'blob' })
      const url = URL.createObjectURL(response.data)
      try {
        const link = document.createElement('a')
        link.href = url
        link.download = `boron-support-${job.id}.zip`
        document.body.appendChild(link)
        link.click()
        link.remove()
      } finally {
        URL.revokeObjectURL(url)
      }
    },
    onSuccess: () => {
      toast.success('Support bundle downloaded', 'The server copy has been removed.')
      queryClient.invalidateQueries({ queryKey: ['support-bundles'] })
    },
    onError: (error) => toast.error('Support bundle could not be downloaded', error.message),
  })
  const latest = jobs.data?.jobs?.[0]

  return (
    <div>
      <PageHeader
        title="Diagnostics"
        description="Run read-only server checks and prepare a bounded, redacted support bundle."
        icon={Stethoscope}
        primaryAction={<Button loading={run.isPending} onClick={() => run.mutate()}><RefreshCw className="h-4 w-4" />Run diagnostics</Button>}
      />

      <section className="border-t border-border py-6" aria-labelledby="diagnostic-results-title">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="diagnostic-results-title" className="text-base font-semibold text-foreground">Diagnostic results</h2>
            <p className="mt-1 text-sm text-muted-foreground">Checks time out independently and never restart services or test remote backup writes.</p>
          </div>
          {result && <Badge variant={statusMeta[result.status]?.variant || 'neutral'}>{statusMeta[result.status]?.label || result.status}</Badge>}
        </div>
        {run.isError && !result ? <ErrorState error={run.error} onRetry={() => run.mutate()} /> : !result ? (
          <EmptyState icon={Stethoscope} title="No diagnostic run yet" description="Run diagnostics to inspect the database, helper, services, storage, DNS, certificates, isolation, backups, and notifications." />
        ) : (
          <div className="divide-y divide-border border-y border-border">
            {result.checks.map((check) => {
              const meta = statusMeta[check.status] || statusMeta.unknown
              const Icon = meta.icon
              return (
                <div key={check.id} className="grid gap-2 py-3 sm:grid-cols-[minmax(180px,0.8fr)_minmax(0,2fr)_auto] sm:items-start">
                  <div className="flex items-center gap-2 text-sm font-medium text-foreground"><Icon className="h-4 w-4" />{check.label}</div>
                  <div>
                    <p className="text-sm text-foreground">{check.explanation}</p>
                    {check.suggested_fix && <p className="mt-1 text-sm text-muted-foreground">Next: {check.suggested_fix}</p>}
                  </div>
                  <Badge variant={meta.variant}>{meta.label}</Badge>
                </div>
              )
            })}
          </div>
        )}
      </section>

      <section className="border-t border-border py-6" aria-labelledby="support-bundle-title">
        <h2 id="support-bundle-title" className="text-base font-semibold text-foreground">Support bundle</h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">Includes allowlisted build/configuration metadata, service states, diagnostics, bounded Boron log tails, failed action traces, error references, and update history. Secrets, keys, mail, and customer files are excluded. The archive expires after seven days and is removed after its first download.</p>
        <label className="mt-4 flex max-w-xl items-start gap-3 text-sm text-foreground">
          <Checkbox checked={includeNames} onCheckedChange={(checked) => setIncludeNames(checked === true)} />
          <span><span className="font-medium">Include account and domain names</span><span className="mt-0.5 block text-sm text-muted-foreground">Names are hashed by default. Enable this only when support needs the real identifiers.</span></span>
        </label>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button variant="secondary" loading={createBundle.isPending} onClick={() => createBundle.mutate()}><Download className="h-4 w-4" />Download support bundle</Button>
          {latest && <span className="text-sm text-muted-foreground">Latest: {latest.progress_message}{latest.file_size ? ` · ${formatBytes(latest.file_size)}` : ''} · expires {formatDate(latest.expires_at)}</span>}
          {latest?.download_available && <Button loading={downloadBundle.isPending} onClick={() => downloadBundle.mutate(latest)}><Download className="h-4 w-4" />Download once</Button>}
        </div>
        {latest?.error && <p className="mt-3 text-sm text-danger">{latest.error}</p>}
        {jobs.isError && <div className="mt-4"><ErrorState error={jobs.error} onRetry={jobs.refetch} /></div>}
      </section>
    </div>
  )
}
