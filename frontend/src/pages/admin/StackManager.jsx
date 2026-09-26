import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ServerCog, Gauge, Code2, Database, RefreshCw } from 'lucide-react'
import { get, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { ProgressBar } from '@/components/ui/Progress'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter } from '@/components/ui/Dialog'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { ErrorState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'

export default function StackManager() {
  const qc = useQueryClient()
  const [preview, setPreview] = useState(null)
  const query = useQuery({ queryKey: ['stack-manager'], queryFn: () => get('/api/v1/admin/stack'), refetchInterval: data => data?.state?.data?.jobs?.some(j => ['queued', 'running'].includes(j.status)) ? 3000 : false })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['stack-manager'] })
  const previewMut = useMutation({ mutationFn: operation => post('/api/v1/admin/stack/preview', operation), onSuccess: setPreview, onError: e => toast.error('Could not prepare maintenance', e.message) })
  const startMut = useMutation({ mutationFn: operation => post('/api/v1/admin/stack/jobs', { ...operation, confirm: true }), onSuccess: () => { toast.success('Stack maintenance queued'); setPreview(null); invalidate() }, onError: e => toast.error('Could not start maintenance', e.message) })
  if (query.isLoading) return <CardSkeleton />
  if (query.error) return <ErrorState error={query.error} onRetry={query.refetch} />
  const data = query.data
  const phpInstalled = new Set(data.components.php.installed.map(item => item.target))
  return <div>
    <PageHeader title="Stack Manager" description="Install and update the supported OpenLiteSpeed, PHP, and MariaDB stack through typed maintenance jobs." icon={ServerCog}><Button variant="secondary" onClick={() => query.refetch()}><RefreshCw className="h-4 w-4" />Refresh</Button></PageHeader>
    <div className="grid gap-4 lg:grid-cols-3">
      <ComponentCard icon={Gauge} title="OpenLiteSpeed" version={data.components.openlitespeed.version} description="Validated configuration and graceful reload after update."><Button onClick={() => previewMut.mutate({ component: 'openlitespeed', action: data.components.openlitespeed.installed ? 'update' : 'install', target: 'latest-supported' })}>{data.components.openlitespeed.installed ? 'Check and update' : 'Install OpenLiteSpeed'}</Button></ComponentCard>
      <ComponentCard icon={Code2} title="PHP runtimes" version={data.components.php.installed.map(item => item.version).join(', ') || 'None'} description="Side-by-side LiteSpeed PHP runtimes and common hosting extensions."><div className="flex flex-wrap gap-2">{data.components.php.targets.map(item => <Button key={item.target} size="sm" variant={phpInstalled.has(item.target) ? 'secondary' : 'primary'} onClick={() => previewMut.mutate({ component: 'php', action: item.installed ? 'update' : 'install', target: item.target })}>{item.installed ? `Update ${item.target}` : `Install ${item.target}`}</Button>)}</div></ComponentCard>
      <ComponentCard icon={Database} title="MariaDB" version={data.components.mariadb.version} description={data.policy.mariadb}><Button onClick={() => previewMut.mutate({ component: 'mariadb', action: data.components.mariadb.installed ? 'update' : 'install', target: '10.11' })}>{data.components.mariadb.installed ? 'Update 10.11 packages' : 'Install MariaDB 10.11'}</Button></ComponentCard>
    </div>
    <Card className="mt-5"><CardHeader><CardTitle>Maintenance history</CardTitle><CardDescription>Only one package transaction runs at a time. Configuration is backed up before a change.</CardDescription></CardHeader><CardContent className="space-y-3">{data.jobs.length ? data.jobs.map(job => <div key={job.id} className="rounded-btn border border-border p-3"><div className="flex items-center justify-between gap-3"><div><strong>{job.component} · {job.target}</strong><p className="text-xs text-muted-foreground">{job.phase.replaceAll('_', ' ')}{job.detail ? ` · ${job.detail}` : ''}</p></div><Badge variant={job.status === 'completed' ? 'success' : job.status === 'failed' ? 'danger' : 'outline'}>{job.status}</Badge></div>{['queued', 'running'].includes(job.status) && <ProgressBar value={job.progress_pct} className="mt-3" />}{job.error && <p className="mt-2 text-sm text-danger">{job.error}</p>}</div>) : <p className="text-sm text-muted-foreground">No stack maintenance has run yet.</p>}</CardContent></Card>
    {preview && <Dialog open onOpenChange={open => !open && setPreview(null)}><DialogContent size="md"><DialogHeader><DialogTitle>{preview.action} {preview.component} {preview.target}</DialogTitle><DialogDescription>{preview.impact}</DialogDescription></DialogHeader><DialogBody className="space-y-3"><div className="rounded-btn border border-border p-3 text-sm"><strong>Packages</strong><p className="mt-1 font-mono text-xs text-muted-foreground">{preview.packages.join(', ')}</p></div><p className="text-sm text-muted-foreground">{preview.rollback}</p>{preview.blockers.map(item => <p key={item} className="text-sm text-danger">{item}</p>)}</DialogBody><DialogFooter><Button variant="secondary" onClick={() => setPreview(null)}>Cancel</Button><Button disabled={preview.blockers.length > 0} loading={startMut.isPending} onClick={() => startMut.mutate(preview)}>Start maintenance</Button></DialogFooter></DialogContent></Dialog>}
  </div>
}

function ComponentCard({ icon: Icon, title, version, description, children }) { return <Card><CardHeader><Icon className="h-7 w-7 text-accent" /><CardTitle>{title}</CardTitle><CardDescription>{description}</CardDescription></CardHeader><CardContent><p className="mb-4 text-sm"><span className="text-muted-foreground">Installed: </span>{version || 'Not detected'}</p>{children}</CardContent></Card> }
