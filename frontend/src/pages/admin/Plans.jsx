import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Layers, Plus, Pencil, Trash2 } from 'lucide-react'
import { get, post, patch, del } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Switch } from '@/components/ui/Toggle'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

const EMPTY_FORM = {
  name: '', cpu_pct: 25, mem_mb: 512, io_mb: 50, pids_max: 50,
  quota_soft_mb: 5120, quota_hard_mb: 6144,
  bandwidth_limit_mb: '', database_limit: '', email_account_limit: '', subdomain_limit: '',
  ftp_account_limit: '', app_limit: '', redis_enabled: false,
}

const NUMERIC_FIELDS = ['cpu_pct', 'mem_mb', 'io_mb', 'pids_max', 'quota_soft_mb', 'quota_hard_mb']
const OPTIONAL_LIMIT_FIELDS = [
  ['bandwidth_limit_mb', 'Bandwidth (MB/mo)'], ['database_limit', 'Max databases'],
  ['email_account_limit', 'Max mailboxes'], ['subdomain_limit', 'Max subdomains'],
  ['ftp_account_limit', 'Max FTP accounts'], ['app_limit', 'Max apps'],
]

function toBody(form) {
  const body = { name: form.name.trim() }
  for (const f of NUMERIC_FIELDS) body[f] = Number(form[f])
  for (const [f] of OPTIONAL_LIMIT_FIELDS) body[f] = form[f] === '' ? null : Number(form[f])
  body.redis_enabled = !!form.redis_enabled
  return body
}

function formFromPlan(plan) {
  const form = { ...EMPTY_FORM, name: plan.name, redis_enabled: plan.redis_enabled }
  for (const f of NUMERIC_FIELDS) form[f] = plan[f]
  for (const [f] of OPTIONAL_LIMIT_FIELDS) form[f] = plan[f] ?? ''
  return form
}

function PlanForm({ form, setForm }) {
  return (
    <div className="space-y-4">
      <FormField label="Plan name" required>
        <Input autoFocus value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="Basic" required />
      </FormField>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <FormField label="CPU %"><Input type="number" min="1" max="100" value={form.cpu_pct} onChange={(e) => setForm((f) => ({ ...f, cpu_pct: e.target.value }))} /></FormField>
        <FormField label="Memory (MB)"><Input type="number" min="64" value={form.mem_mb} onChange={(e) => setForm((f) => ({ ...f, mem_mb: e.target.value }))} /></FormField>
        <FormField label="Disk IO (MB/s)"><Input type="number" min="1" value={form.io_mb} onChange={(e) => setForm((f) => ({ ...f, io_mb: e.target.value }))} /></FormField>
        <FormField label="Max processes"><Input type="number" min="10" value={form.pids_max} onChange={(e) => setForm((f) => ({ ...f, pids_max: e.target.value }))} /></FormField>
        <FormField label="Disk soft quota (MB)"><Input type="number" min="1" value={form.quota_soft_mb} onChange={(e) => setForm((f) => ({ ...f, quota_soft_mb: e.target.value }))} /></FormField>
        <FormField label="Disk hard quota (MB)"><Input type="number" min="1" value={form.quota_hard_mb} onChange={(e) => setForm((f) => ({ ...f, quota_hard_mb: e.target.value }))} /></FormField>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {OPTIONAL_LIMIT_FIELDS.map(([key, label]) => (
          <FormField key={key} label={label} hint="Blank = unlimited">
            <Input type="number" min="1" placeholder="Unlimited" value={form[key]} onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} />
          </FormField>
        ))}
      </div>
      <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
        <div className="text-sm">
          <div className="font-medium text-foreground">Redis</div>
          <div className="text-muted-foreground">Accounts on this plan get per-account Redis enabled.</div>
        </div>
        <Switch checked={form.redis_enabled} onCheckedChange={(v) => setForm((f) => ({ ...f, redis_enabled: v }))} />
      </div>
    </div>
  )
}

export default function Plans() {
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState(null) // plan object | null
  const [form, setForm] = useState(EMPTY_FORM)
  const [toDelete, setToDelete] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['plans'],
    queryFn: () => get('/api/v1/admin/plans'),
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['plans'] })

  const createMut = useMutation({
    mutationFn: (body) => post('/api/v1/admin/plans', body),
    onSuccess: () => {
      toast.success('Plan created')
      invalidate()
      setCreateOpen(false)
      setForm(EMPTY_FORM)
    },
    onError: (e) => toast.error('Could not create plan', e.message),
  })

  const updateMut = useMutation({
    mutationFn: ({ id, body }) => patch(`/api/v1/admin/plans/${id}`, body),
    onSuccess: () => {
      toast.success('Plan updated')
      invalidate()
      setEditing(null)
    },
    onError: (e) => toast.error('Could not update plan', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (p) => del(`/api/v1/admin/plans/${p.id}`),
    onSuccess: () => {
      toast.success('Plan deleted')
      invalidate()
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not delete plan', e.message),
  })

  const columns = [
    { key: 'name', header: 'Name', sortable: true, searchable: true, render: (r) => <span className="font-medium text-foreground">{r.name}</span> },
    { key: 'cpu_pct', header: 'CPU', render: (r) => `${r.cpu_pct}%` },
    { key: 'mem_mb', header: 'RAM', render: (r) => `${r.mem_mb} MB` },
    { key: 'quota_hard_mb', header: 'Disk', render: (r) => `${r.quota_hard_mb} MB` },
    { key: 'bandwidth_limit_mb', header: 'Bandwidth', render: (r) => r.bandwidth_limit_mb ? `${r.bandwidth_limit_mb} MB` : <span className="text-muted-foreground">Unlimited</span> },
    { key: 'redis_enabled', header: 'Redis', render: (r) => r.redis_enabled ? 'Yes' : <span className="text-muted-foreground">No</span> },
    {
      key: 'actions', header: '', align: 'right', searchable: false,
      render: (r) => (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="icon-sm" aria-label={`Edit ${r.name}`} onClick={() => { setEditing(r); setForm(formFromPlan(r)) }}>
            <Pencil className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon-sm" aria-label={`Delete ${r.name}`} onClick={() => setToDelete(r)}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Plans"
        description="Named limit presets you can apply to any hosting account -- CPU, RAM, IO, disk, bandwidth, mailbox/database/subdomain/FTP/app caps, and Redis."
        icon={Layers}
      >
        <Button onClick={() => { setForm(EMPTY_FORM); setCreateOpen(true) }}>
          <Plus className="h-4 w-4" /> New plan
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={data?.plans}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search plans…"
        pageSize={15}
        getRowKey={(r) => r.id}
        emptyTitle="No plans yet"
        emptyDescription="Create a named preset (Basic, Pro, Business…) to apply consistent limits when creating or updating accounts."
        emptyIcon={Layers}
        emptyAction={<Button onClick={() => { setForm(EMPTY_FORM); setCreateOpen(true) }}><Plus className="h-4 w-4" /> New plan</Button>}
      />

      {/* Create */}
      <Dialog open={createOpen} onOpenChange={(v) => { setCreateOpen(v); if (!v) setForm(EMPTY_FORM) }}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>New plan</DialogTitle>
            <DialogDescription>Define a named limit preset. It won't affect any existing account until applied.</DialogDescription>
          </DialogHeader>
          <form onSubmit={(e) => { e.preventDefault(); createMut.mutate(toBody(form)) }}>
            <DialogBody><PlanForm form={form} setForm={setForm} /></DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending} disabled={!form.name.trim()}>Create plan</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Edit */}
      <Dialog open={!!editing} onOpenChange={(v) => { if (!v) setEditing(null) }}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>Edit {editing?.name}</DialogTitle>
            <DialogDescription>Changes only apply to accounts the next time this plan is applied to them.</DialogDescription>
          </DialogHeader>
          <form onSubmit={(e) => { e.preventDefault(); editing && updateMut.mutate({ id: editing.id, body: toBody(form) }) }}>
            <DialogBody><PlanForm form={form} setForm={setForm} /></DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
              <Button type="submit" loading={updateMut.isPending} disabled={!form.name.trim()}>Save changes</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete */}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => { if (!v) setToDelete(null) }}
        title={toDelete ? `Delete plan "${toDelete.name}"?` : 'Delete plan?'}
        description="Accounts currently on this plan keep their existing limits -- only the plan record itself, and the label on those accounts, is removed."
        confirmLabel="Delete plan"
        loading={deleteMut.isPending}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />
    </div>
  )
}
