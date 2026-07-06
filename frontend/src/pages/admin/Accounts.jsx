import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Users, ShieldCheck } from 'lucide-react'
import { get, post } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

export default function Accounts() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ username: '', primary_domain: '' })

  // Namespace bulk-enable (admin migration). Trigger returns a job we then
  // poll every 2s while it runs; progress is surfaced in the ConfirmDialog.
  const [nsOpen, setNsOpen] = useState(false)
  const [jobId, setJobId] = useState(null)
  const notifiedRef = useRef(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => get('/api/v1/accounts'),
  })

  const createMut = useMutation({
    mutationFn: (body) => post('/api/v1/accounts', body),
    onSuccess: (acc) => {
      toast.success('Account created', `${acc.username} is being provisioned.`)
      qc.invalidateQueries({ queryKey: ['accounts'] })
      setOpen(false)
      setForm({ username: '', primary_domain: '' })
      navigate(`/accounts/${acc.username}`)
    },
    onError: (e) => toast.error('Could not create account', e.message),
  })

  const bulkMut = useMutation({
    mutationFn: () => post('/api/v1/accounts/namespace/bulk-enable'),
    onSuccess: (job) => { notifiedRef.current = null; setJobId(job.id) },
    onError: (e) => toast.error('Could not start bulk-enable', e.message),
  })

  const { data: job } = useQuery({
    queryKey: ['ns-bulk-enable', jobId],
    queryFn: () => get(`/api/v1/accounts/namespace/bulk-enable/${jobId}`),
    enabled: jobId != null,
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'pending' || s === 'running' ? 2000 : false
    },
  })

  const running = bulkMut.isPending || (!!job && (job.status === 'pending' || job.status === 'running'))
  const finished = !!job && (job.status === 'completed' || job.status === 'failed')

  // Fire a single toast (and refresh accounts) when a job reaches a terminal
  // state, so completion is visible even if the dialog is closed mid-run.
  useEffect(() => {
    if (!finished || notifiedRef.current === job.id) return
    notifiedRef.current = job.id
    if (job.status === 'completed') {
      toast.success('Namespace isolation enabled', `${job.completed_count} / ${job.total} accounts enabled.`)
    } else {
      toast.error('Bulk-enable stopped', job.error || `Stopped after ${job.completed_count} / ${job.total} accounts.`)
    }
    qc.invalidateQueries({ queryKey: ['accounts'] })
  }, [finished, job, qc])

  function resetBulk() {
    setJobId(null)
    notifiedRef.current = null
    bulkMut.reset()
  }

  function handleNsConfirm() {
    if (finished) { setNsOpen(false); resetBulk(); return }
    if (!jobId) bulkMut.mutate()
  }

  let nsDescription
  if (finished && job.status === 'completed') {
    nsDescription = <>Done — <strong className="text-foreground">{job.completed_count} / {job.total}</strong> accounts enabled.</>
  } else if (finished) {
    nsDescription = (
      <>Stopped after <strong className="text-foreground">{job.completed_count} / {job.total}</strong> accounts.<br />{job.error}</>
    )
  } else if (running) {
    nsDescription = (
      <>
        Enabling isolation… <strong className="text-foreground">{job?.completed_count ?? 0} / {job?.total ?? '…'}</strong> accounts done.
        {job?.current_username ? <><br />Currently: {job.current_username}</> : null}
      </>
    )
  } else {
    nsDescription = 'Enables mount-namespace isolation for every currently active account that is not already enabled, one at a time. It stops at the first account that fails verification. This runs in the background and may take a while.'
  }

  const columns = [
    { key: 'username', header: 'Username', sortable: true, searchable: true, render: (r) => <span className="font-medium text-foreground">{r.username}</span> },
    { key: 'status', header: 'Status', sortable: true, render: (r) => <StatusBadge status={r.status} /> },
    { key: 'primary_domain', header: 'Primary domain', searchable: true, render: (r) => r.primary_domain || <span className="text-muted-foreground">—</span> },
    { key: 'created_at', header: 'Created', sortable: true, render: (r) => (r.created_at ? formatDate(r.created_at) : '—') },
  ]

  return (
    <div>
      <PageHeader title="Accounts" description="Manage all hosting accounts on this server." icon={Users}>
        <Button variant="secondary" onClick={() => setNsOpen(true)}>
          <ShieldCheck className="h-4 w-4" /> Namespace: bulk-enable
        </Button>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Create account
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={data}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search accounts…"
        pageSize={15}
        getRowKey={(r) => r.username}
        onRowClick={(r) => navigate(`/accounts/${r.username}`)}
        emptyTitle="No accounts yet"
        emptyDescription="Create your first hosting account to get started."
        emptyIcon={Users}
        emptyAction={<Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> Create account</Button>}
      />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Create hosting account</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({ username: form.username, primary_domain: form.primary_domain || undefined })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Username" required hint="Lowercase letters and digits, starts with a letter (max 16 chars).">
                <Input
                  autoFocus
                  value={form.username}
                  onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
                  pattern="[a-z][a-z0-9]{0,15}"
                  placeholder="acme1"
                  required
                />
              </FormField>
              <FormField label="Primary domain" hint="Optional — can be added later.">
                <Input
                  value={form.primary_domain}
                  onChange={(e) => setForm((f) => ({ ...f, primary_domain: e.target.value }))}
                  placeholder="example.com"
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending}>Create account</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={nsOpen}
        onOpenChange={(v) => { setNsOpen(v); if (!v && !running) resetBulk() }}
        title="Enable namespace isolation for all accounts"
        description={nsDescription}
        confirmLabel={finished ? 'Close' : running ? 'Enabling…' : 'Enable for all eligible'}
        variant="primary"
        loading={running}
        onConfirm={handleNsConfirm}
      />
    </div>
  )
}
