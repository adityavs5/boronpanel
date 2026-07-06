import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Clock, Plus, Trash2, Pencil, MoreHorizontal, Mail, Save } from 'lucide-react'
import { get, post, patch, put, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField, Label } from '@/components/ui/Input'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'
import { toast } from '@/components/ui/Toast'

const SCHEDULE_FIELDS = [
  ['minute', 'Minute'],
  ['hour', 'Hour'],
  ['dom', 'Day of month'],
  ['month', 'Month'],
  ['dow', 'Day of week'],
]

const EMPTY_FORM = { minute: '*', hour: '*', dom: '*', month: '*', dow: '*', raw: '', command: '', label: '' }

export default function Cron() {
  const username = useAccountUsername()
  const qc = useQueryClient()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState(null) // job being edited, or null for create
  const [form, setForm] = useState(EMPTY_FORM)
  const [toDelete, setToDelete] = useState(null)
  const [mailto, setMailto] = useState('')

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['crons', username],
    queryFn: () => get(`/api/v1/accounts/${username}/crons`),
    enabled: !!username,
  })

  const mailtoQuery = useQuery({
    queryKey: ['cron-mailto', username],
    queryFn: () => get(`/api/v1/accounts/${username}/crons/mailto`),
    enabled: !!username,
  })

  // Seed the MAILTO input once the current value loads.
  useEffect(() => {
    if (mailtoQuery.data) setMailto(mailtoQuery.data.mailto || '')
  }, [mailtoQuery.data])

  const usingRaw = !!form.raw.trim()
  const builderValid = [form.minute, form.hour, form.dom, form.month, form.dow].every((s) => s.trim())
  const composedSchedule = usingRaw
    ? form.raw.trim()
    : builderValid
      ? [form.minute, form.hour, form.dom, form.month, form.dow].map((s) => s.trim()).join(' ')
      : ''

  const saveMut = useMutation({
    mutationFn: (body) =>
      editing
        ? put(`/api/v1/accounts/${username}/crons/${editing.id}`, body)
        : post(`/api/v1/accounts/${username}/crons`, body),
    onSuccess: () => {
      toast.success(editing ? 'Cron job updated' : 'Cron job added')
      qc.invalidateQueries({ queryKey: ['crons', username] })
      setDialogOpen(false)
      setEditing(null)
      setForm(EMPTY_FORM)
    },
    onError: (e) => toast.error(editing ? 'Could not update cron job' : 'Could not add cron job', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (job) => del(`/api/v1/accounts/${username}/crons/${job.id}`),
    onSuccess: () => {
      toast.success('Cron job deleted')
      qc.invalidateQueries({ queryKey: ['crons', username] })
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not delete cron job', e.message),
  })

  const mailtoMut = useMutation({
    mutationFn: (value) => patch(`/api/v1/accounts/${username}/crons/mailto`, { mailto: value }),
    onSuccess: () => {
      toast.success('Cron email saved')
      qc.invalidateQueries({ queryKey: ['cron-mailto', username] })
    },
    onError: (e) => toast.error('Could not save cron email', e.message),
  })

  function openCreate() {
    setEditing(null)
    setForm(EMPTY_FORM)
    setDialogOpen(true)
  }

  function openEdit(job) {
    const parts = (job.schedule || '').trim().split(/\s+/)
    if (parts.length === 5) {
      setForm({
        minute: parts[0], hour: parts[1], dom: parts[2], month: parts[3], dow: parts[4],
        raw: '', command: job.command || '', label: job.label || '',
      })
    } else {
      // Special forms like @daily / @reboot land in the raw override.
      setForm({ ...EMPTY_FORM, raw: job.schedule || '', command: job.command || '', label: job.label || '' })
    }
    setEditing(job)
    setDialogOpen(true)
  }

  function submit(e) {
    e.preventDefault()
    saveMut.mutate({ schedule: composedSchedule, command: form.command.trim(), label: form.label.trim() })
  }

  const columns = [
    {
      key: 'label',
      header: 'Label',
      sortable: true,
      searchable: true,
      render: (r) =>
        r.label
          ? <span className="font-medium text-foreground">{r.label}</span>
          : <span className="text-muted-foreground">Untitled</span>,
    },
    {
      key: 'schedule',
      header: 'Schedule',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-mono text-xs text-foreground">{r.schedule}</span>,
    },
    {
      key: 'command',
      header: 'Command',
      searchable: true,
      cellClassName: 'max-w-[22rem]',
      render: (r) => (
        <span className="block truncate font-mono text-xs text-muted-foreground" title={r.command}>
          {r.command}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => (
        <div className="flex justify-end">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.label || 'cron job'}`}>
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onSelect={() => openEdit(r)}>
                <Pencil className="h-4 w-4" /> Edit
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setToDelete(r)}>
                <Trash2 className="h-4 w-4" /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Cron jobs"
        description="Scheduled commands that run as your account's Linux user via the system crontab."
        icon={Clock}
      >
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4" /> Add cron job
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={data?.jobs}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search cron jobs…"
        pageSize={15}
        getRowKey={(r) => r.id}
        emptyTitle="No cron jobs yet"
        emptyDescription="Schedule a command to run automatically on a recurring basis."
        emptyIcon={Clock}
        emptyAction={<Button onClick={openCreate}><Plus className="h-4 w-4" /> Add cron job</Button>}
      />

      {/* Cron output email (MAILTO) */}
      <Card className="mt-6 max-w-xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Mail className="h-4 w-4 text-muted-foreground" /> Cron output email
          </CardTitle>
          <CardDescription>
            Where output and errors from your cron jobs are emailed. Leave blank to use the system default.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              mailtoMut.mutate(mailto.trim())
            }}
          >
            <FormField label="MAILTO" htmlFor="cron-mailto" className="flex-1">
              <Input
                id="cron-mailto"
                type="email"
                value={mailto}
                onChange={(e) => setMailto(e.target.value)}
                placeholder="you@yourdomain.com"
                disabled={mailtoQuery.isLoading}
              />
            </FormField>
            <Button type="submit" loading={mailtoMut.isPending} disabled={mailtoQuery.isLoading}>
              <Save className="h-4 w-4" /> Save
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* Create / edit cron job */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>{editing ? 'Edit cron job' : 'Add cron job'}</DialogTitle>
            <DialogDescription>
              Each schedule field takes a number, <code className="font-mono">*</code>, a range like{' '}
              <code className="font-mono">1-5</code>, a list like <code className="font-mono">1,3,5</code>, or a step like{' '}
              <code className="font-mono">*/5</code>.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={submit}>
            <DialogBody className="space-y-4">
              <div>
                <Label>Schedule</Label>
                <div className="mt-1.5 grid grid-cols-5 gap-2">
                  {SCHEDULE_FIELDS.map(([key, label]) => (
                    <div key={key} className="space-y-1">
                      <Label className="text-xs font-normal text-muted-foreground">{label}</Label>
                      <Input
                        value={form[key]}
                        onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                        disabled={usingRaw}
                        className="px-2 text-center font-mono"
                      />
                    </div>
                  ))}
                </div>
              </div>

              <FormField
                label="Raw schedule (advanced)"
                hint="Overrides the fields above when set. Supports special forms like @daily or @reboot."
              >
                <Input
                  value={form.raw}
                  onChange={(e) => setForm((f) => ({ ...f, raw: e.target.value }))}
                  placeholder="e.g. 0 3 * * 0"
                  className="font-mono"
                />
              </FormField>

              <div className="rounded-btn border border-border bg-muted px-3 py-2 text-xs">
                <span className="text-muted-foreground">Runs at: </span>
                <code className="font-mono text-foreground">{composedSchedule || '—'}</code>
              </div>

              <FormField label="Command" required hint="The full command to run.">
                <Input
                  value={form.command}
                  onChange={(e) => setForm((f) => ({ ...f, command: e.target.value }))}
                  placeholder={`/usr/bin/php /home/${username || 'user'}/cron.php`}
                  className="font-mono"
                  required
                />
              </FormField>

              <FormField label="Label" hint="Optional — a short name to identify this job.">
                <Input
                  value={form.label}
                  onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
                  placeholder="Nightly cleanup"
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setDialogOpen(false)}>Cancel</Button>
              <Button type="submit" loading={saveMut.isPending} disabled={!composedSchedule || !form.command.trim()}>
                {editing ? 'Save changes' : 'Add job'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete */}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => { if (!v) setToDelete(null) }}
        title="Delete cron job?"
        description={
          toDelete
            ? `The scheduled command "${toDelete.label || toDelete.command}" will be removed from your crontab. This cannot be undone.`
            : 'This cron job will be permanently removed.'
        }
        confirmLabel="Delete job"
        loading={deleteMut.isPending}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />
    </div>
  )
}
