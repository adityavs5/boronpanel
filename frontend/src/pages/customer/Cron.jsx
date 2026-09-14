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

// Item 6: quick presets for the schedule builder. Fields mirror
// daemon/cron.py's describe_schedule() so the "Runs at" preview below the
// builder reads the same way the saved job's description will once it
// comes back from the API. "Custom" just clears the raw override and lets
// the five fields underneath speak for themselves.
const SCHEDULE_TEMPLATES = [
  { label: 'Every minute', fields: { minute: '*', hour: '*', dom: '*', month: '*', dow: '*' } },
  { label: 'Every 5 minutes', fields: { minute: '*/5', hour: '*', dom: '*', month: '*', dow: '*' } },
  { label: 'Every 15 minutes', fields: { minute: '*/15', hour: '*', dom: '*', month: '*', dow: '*' } },
  { label: 'Every 30 minutes', fields: { minute: '*/30', hour: '*', dom: '*', month: '*', dow: '*' } },
  { label: 'Hourly', fields: { minute: '0', hour: '*', dom: '*', month: '*', dow: '*' } },
  { label: 'Daily', fields: { minute: '0', hour: '0', dom: '*', month: '*', dow: '*' } },
  { label: 'Weekly', fields: { minute: '0', hour: '0', dom: '*', month: '*', dow: '0' } },
  { label: 'Monthly', fields: { minute: '0', hour: '0', dom: '1', month: '*', dow: '*' } },
  { label: 'Custom', fields: null },
]

// Mirrors daemon/cron.py's describe_schedule() for an instant preview while
// editing, before the job is saved and the authoritative server-computed
// description comes back from the API. Kept intentionally small — only the
// same fixed set of shapes the backend recognizes; anything else falls back
// to "Custom schedule" on both sides.
const WEEKDAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const MONTH_NAMES = [
  null, 'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

function describeScheduleLocally(schedule) {
  const nicknames = {
    '@yearly': 'Once a year, at midnight on January 1st',
    '@annually': 'Once a year, at midnight on January 1st',
    '@monthly': 'Once a month, at midnight on the 1st',
    '@weekly': 'Once a week, at midnight on Sunday',
    '@daily': 'Every day at midnight',
    '@midnight': 'Every day at midnight',
    '@hourly': 'Every hour, on the hour',
  }
  const trimmed = (schedule || '').trim()
  if (nicknames[trimmed]) return nicknames[trimmed]

  const parts = trimmed.split(/\s+/)
  if (parts.length !== 5) return 'Custom schedule'
  const [minute, hour, dom, month, dow] = parts
  const isDigits = (s) => /^\d+$/.test(s)
  const fmtTime = (h, m) => `${String(parseInt(h, 10)).padStart(2, '0')}:${String(parseInt(m, 10)).padStart(2, '0')}`

  if (minute === '*' && hour === '*' && dom === '*' && month === '*' && dow === '*') return 'Every minute'

  const step = /^\*\/(\d+)$/.exec(minute)
  if (step && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    const n = parseInt(step[1], 10)
    return n <= 1 ? 'Every minute' : `Every ${n} minutes`
  }

  if (isDigits(minute) && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    return minute === '0' ? 'Every hour, on the hour' : `Every hour, at minute ${parseInt(minute, 10)}`
  }

  if (isDigits(minute) && isDigits(hour) && dom === '*' && month === '*' && dow === '*') {
    return `Every day at ${fmtTime(hour, minute)}`
  }

  if (isDigits(minute) && isDigits(hour) && isDigits(dow) && dom === '*' && month === '*') {
    return `Every ${WEEKDAY_NAMES[parseInt(dow, 10) % 7]} at ${fmtTime(hour, minute)}`
  }

  if (isDigits(minute) && isDigits(hour) && isDigits(dom) && month === '*' && dow === '*') {
    return `On day ${parseInt(dom, 10)} of every month at ${fmtTime(hour, minute)}`
  }

  if (isDigits(minute) && isDigits(hour) && isDigits(dom) && isDigits(month) && dow === '*') {
    const mi = parseInt(month, 10)
    const name = mi >= 1 && mi <= 12 ? MONTH_NAMES[mi] : month
    return `Once a year on ${name} ${parseInt(dom, 10)} at ${fmtTime(hour, minute)}`
  }

  return 'Custom schedule'
}

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
      render: (r) => <button type="button" className="font-medium text-accent hover:underline text-left" onClick={() => openEdit(r)} aria-label={`Edit cron job ${r.label || r.command}`}>{r.label || 'Untitled'}</button>,
    },
    {
      key: 'schedule',
      header: 'Schedule',
      sortable: true,
      searchable: true,
      render: (r) => (
        <div>
          <div className="text-foreground">{r.description || describeScheduleLocally(r.schedule)}</div>
          <div className="font-mono text-xs text-muted-foreground">{r.schedule}</div>
        </div>
      ),
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
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => openEdit(r)}>Edit</Button>
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
                <Label>Template</Label>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {SCHEDULE_TEMPLATES.map((t) => {
                    const active = t.fields
                      ? !usingRaw && SCHEDULE_FIELDS.every(([key]) => form[key] === t.fields[key])
                      : usingRaw
                    return (
                      <Button
                        key={t.label}
                        type="button"
                        size="sm"
                        variant={active ? 'primary' : 'outline'}
                        onClick={() => {
                          if (t.fields) setForm((f) => ({ ...f, ...t.fields, raw: '' }))
                          // "Custom" leaves the current field values as-is and just
                          // focuses attention on the raw override below.
                        }}
                      >
                        {t.label}
                      </Button>
                    )
                  })}
                </div>
              </div>

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
                <div className="text-foreground">
                  {composedSchedule ? describeScheduleLocally(composedSchedule) : '—'}
                </div>
                <div className="mt-0.5">
                  <span className="text-muted-foreground">Raw: </span>
                  <code className="font-mono text-foreground">{composedSchedule || '—'}</code>
                </div>
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
