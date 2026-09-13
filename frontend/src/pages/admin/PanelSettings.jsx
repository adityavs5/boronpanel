import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Network, ExternalLink } from 'lucide-react'
import { get, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { FormField, Input } from '@/components/ui/Input'
import { CenteredSpinner } from '@/components/ui/Spinner'

function address(port) {
  const url = new URL(window.location.href)
  url.protocol = 'https:'
  url.port = String(port)
  url.pathname = '/app/panel-settings'
  url.search = ''
  url.hash = ''
  return url.href
}

export default function PanelSettings() {
  const [form, setForm] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [submitted, setSubmitted] = useState(null)
  const query = useQuery({ queryKey: ['panel-config'], queryFn: () => get('/api/v1/admin/panel-config'),
    refetchInterval: q => {
      const latest = q.state.data?.jobs?.find(job => job.id === submitted?.id) ?? submitted
      return ['pending', 'running'].includes(latest?.status) || q.state.data?.jobs?.some(job => ['pending', 'running'].includes(job.status)) ? 3000 : false
    }, retry: false })
  // Polling also resumes when opening the new address during a queued change.
  const data = query.data
  const active = data?.jobs?.find(job => ['pending', 'running'].includes(job.status))
  const values = form ?? { admin_port: data?.admin_port ?? 2222, customer_port: data?.customer_port ?? 2222 }
  const currentJob = data?.jobs?.find(job => job.id === submitted?.id) ?? submitted ?? active
  const mutation = useMutation({ mutationFn: body => post('/api/v1/admin/panel-config/ports', body),
    onSuccess: job => { setSubmitted(job); setConfirmed(false); query.refetch() } })
  if (query.isLoading) return <CenteredSpinner />
  const valid = [values.admin_port, values.customer_port].every(port => Number.isInteger(port) && port >= 1024 && port <= 65535)
  const changed = values.admin_port !== data?.admin_port || values.customer_port !== data?.customer_port
  const busy = mutation.isPending || !!active || ['pending', 'running'].includes(currentJob?.status)
  return <div className="space-y-6">
    <PageHeader title="Panel settings" description="Choose how administrators and customers access your panel." icon={Network} />
    <Card><CardHeader><div className="space-y-1"><CardTitle>Panel access</CardTitle><CardDescription>Use the same port for everyone, or separate administrator and customer access. Both use HTTPS.</CardDescription></div></CardHeader>
      <CardContent className="space-y-5">
        {query.error && <p role="status" className="text-sm text-muted-foreground">{submitted ? 'The panel may be restarting. Open the new administrator address below. If the change fails, the previous address is restored.' : query.error.message}</p>}
        <div className="grid gap-4 sm:grid-cols-2">{[['admin_port', 'Administrator port'], ['customer_port', 'Customer port']].map(([key, label]) => <FormField key={key} label={label} htmlFor={key}>
          <Input id={key} type="number" min={1024} max={65535} step={1} disabled={busy} value={values[key]} onChange={event => { setForm({ ...values, [key]: event.target.value === '' ? '' : Number(event.target.value) }); setConfirmed(false) }} />
        </FormField>)}</div>
        <p className="text-sm text-muted-foreground">Default: 2222. Choose a port from 1024 to 65535. The panel checks availability and opens the local firewall. If your provider has an external firewall, allow both ports there first.</p>
        {valid && <div className="rounded-btn border border-border bg-input-surface p-4 text-sm space-y-2 break-all">
          <p><strong>Administrator:</strong> {address(values.admin_port)}</p>
          <p><strong>Customer:</strong> {address(values.customer_port).replace('/panel-settings', '/dashboard')}</p>
        </div>}
        <label className="flex items-start gap-3 text-sm"><input type="checkbox" className="mt-1" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} /><span>I have saved the new addresses and allowed the ports in any external firewall. Applying restarts the panel briefly.</span></label>
        {mutation.error && <p role="alert" className="text-sm text-danger">{mutation.error.message}</p>}
        <Button loading={mutation.isPending} disabled={!valid || !changed || !confirmed || busy || !data} onClick={() => mutation.mutate({ ...values, confirm: true })}>Apply panel ports</Button>
      </CardContent>
    </Card>
    {currentJob && <Card><CardHeader><CardTitle>{currentJob.status === 'completed' ? 'Panel ports updated' : currentJob.status === 'failed' ? 'Port change failed' : 'Applying panel ports'}</CardTitle></CardHeader><CardContent className="space-y-4">
      <p role="status" className="text-sm">{currentJob.error || (currentJob.status === 'completed' ? 'Both HTTPS listeners passed their health checks.' : 'Allow up to a minute for the restart and health checks, then open the new administrator address. If verification fails, the panel restores the previous configuration.')}</p>
      {currentJob.status !== 'failed' && <a className="inline-flex items-center gap-2 text-accent underline break-all" href={address(currentJob.admin_port)}>Open new administrator address <ExternalLink className="h-4 w-4 shrink-0" /></a>}
    </CardContent></Card>}
    {!!data?.jobs?.length && <Card><CardHeader><CardTitle>Recent changes</CardTitle></CardHeader><CardContent><ul className="divide-y divide-border">{data.jobs.map(job => <li key={job.id} className="py-3 text-sm"><div className="flex flex-wrap justify-between gap-2"><span>Admin {job.admin_port} · Customer {job.customer_port}</span><strong className="capitalize">{job.status}</strong></div><p className="text-muted-foreground">{new Date(job.created_at).toLocaleString()} · {job.initiated_by}</p>{job.error && <p className="mt-1" role="status">{job.error}</p>}</li>)}</ul></CardContent></Card>}
  </div>
}
