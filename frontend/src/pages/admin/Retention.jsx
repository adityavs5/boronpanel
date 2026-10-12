import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArchiveRestore, Save, Trash2 } from 'lucide-react'
import { get, patch, post } from '@/lib/api'
import { formatBytes, titleCase } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Switch } from '@/components/ui/Toggle'
import { ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

const FIELDS = [
  ['audit_days', 'Audit history', 'Actor, result, request ID and change history.'],
  ['action_step_days', 'Action step output', 'Redacted stdout/stderr tails and step timing.'],
  ['task_output_days', 'Finished tasks', 'Completed and failed job/task rows.'],
  ['usage_raw_days', 'Raw usage samples', 'Daily samples are rolled into monthly aggregates before removal.'],
  ['usage_monthly_days', 'Monthly usage summaries', 'Compact monthly usage history.'],
  ['bandwidth_days', 'Bandwidth history', 'Account and domain daily bandwidth totals.'],
  ['daemon_log_days', 'Rotated Boron logs', 'Compressed and numbered logs under the Boron log directory.'],
  ['support_bundle_days', 'Support bundles', 'Generated diagnostic archives; download links may expire sooner after use.'],
]

export default function Retention() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['retention-settings'], queryFn: () => get('/api/v1/admin/retention') })
  const [form, setForm] = useState(null)
  const [cleanupOpen, setCleanupOpen] = useState(false)
  useEffect(() => {
    if (query.data) setForm(Object.fromEntries(FIELDS.map(([key]) => [key, query.data[key]])))
  }, [query.data])
  const dirty = useMemo(() => !!query.data && !!form && FIELDS.some(([key]) => Number(form[key]) !== Number(query.data[key])), [query.data, form])
  const save = useMutation({
    mutationFn: body => patch('/api/v1/admin/retention', body),
    onSuccess: data => { qc.setQueryData(['retention-settings'], old => ({ ...old, ...data })); toast.success('Retention settings saved') },
    onError: error => toast.error('Could not save retention settings', error.message),
  })
  const hold = useMutation({
    mutationFn: value => patch('/api/v1/admin/retention', { investigation_hold: value }),
    onSuccess: data => { qc.setQueryData(['retention-settings'], old => ({ ...old, ...data })); toast.success(data.investigation_hold ? 'Investigation hold enabled' : 'Investigation hold removed') },
    onError: error => toast.error('Could not change investigation hold', error.message),
  })
  const cleanup = useMutation({
    mutationFn: () => post('/api/v1/admin/retention/prune'),
    onSuccess: result => {
      setCleanupOpen(false)
      qc.invalidateQueries({ queryKey: ['retention-settings'] })
      const total = Object.values(result.removed || {}).reduce((sum, value) => sum + Number(value || 0), 0)
      toast.success('Retention cleanup completed', `${total} expired rows or files removed.`)
    },
    onError: error => toast.error('Retention cleanup failed', error.message),
  })

  return <div>
    <PageHeader title="Data retention" description="Control how long Boron keeps operational history and diagnostic artifacts." icon={ArchiveRestore} />
    {query.isLoading ? <CardSkeleton /> : query.error ? <ErrorState error={query.error} onRetry={query.refetch} /> : <div className="space-y-6">
      <Card>
        <CardHeader><div><CardTitle>Investigation hold</CardTitle><CardDescription>Pause every automated retention cleanup while an incident is being investigated.</CardDescription></div></CardHeader>
        <CardContent className="flex items-center gap-3">
          <Switch checked={!!query.data.investigation_hold} disabled={hold.isPending} onCheckedChange={value => hold.mutate(value)} aria-label="Investigation hold" />
          <span className="text-sm text-foreground">{query.data.investigation_hold ? 'Cleanup paused' : 'Normal cleanup active'}</span>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><div><CardTitle>Retention periods</CardTitle><CardDescription>Protected minimums preserve a useful troubleshooting window. Values are in days.</CardDescription></div></CardHeader>
        <CardContent className="space-y-5">
          {FIELDS.map(([key, label, description]) => <FormField key={key} label={label} hint={`${description} Minimum ${query.data.minimums?.[key]} days.`}>
            <Input type="number" min={query.data.minimums?.[key]} max="3650" className="max-w-[12rem]" value={form?.[key] ?? ''} onChange={event => setForm(current => ({ ...current, [key]: event.target.value }))} />
          </FormField>)}
        </CardContent>
        <CardFooter><Button loading={save.isPending} disabled={!dirty || save.isPending} onClick={() => save.mutate(Object.fromEntries(FIELDS.map(([key]) => [key, Number(form[key])]))) }><Save className="h-4 w-4" /> Save retention</Button></CardFooter>
      </Card>
      <Card>
        <CardHeader><div><CardTitle>Stored history</CardTitle><CardDescription>Current control-plane database size and row counts.</CardDescription></div></CardHeader>
        <CardContent>
          <dl className="divide-y divide-border text-sm">
            <div className="flex justify-between gap-4 py-2"><dt className="text-muted-foreground">Database</dt><dd className="font-medium tabular-nums">{query.data.table_sizes?.database_bytes == null ? 'n/a' : formatBytes(query.data.table_sizes.database_bytes)}</dd></div>
            {Object.entries(query.data.table_sizes?.rows || {}).map(([name, count]) => <div key={name} className="flex justify-between gap-4 py-2"><dt className="text-muted-foreground">{titleCase(name.replaceAll('_', ' '))}</dt><dd className="font-medium tabular-nums">{Number(count).toLocaleString()}</dd></div>)}
          </dl>
        </CardContent>
        <CardFooter><Button variant="outline" disabled={!!query.data.investigation_hold} title={query.data.investigation_hold ? 'Remove the investigation hold before cleanup.' : undefined} onClick={() => setCleanupOpen(true)}><Trash2 className="h-4 w-4" /> Run cleanup</Button></CardFooter>
      </Card>
    </div>}
    <ConfirmDialog open={cleanupOpen} onOpenChange={setCleanupOpen} title="Remove expired history?" description="Boron will remove only data older than the configured retention periods, in bounded batches. This cannot be undone." confirmLabel="Run cleanup" loading={cleanup.isPending} loadingDescription="Removing expired history in bounded batches…" onConfirm={() => cleanup.mutate()} />
  </div>
}
