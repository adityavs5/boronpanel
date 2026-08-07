import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Mail, Plus, Trash2, Inbox, Forward, ShieldAlert, AtSign, Network, ScrollText,
  ShieldBan, ShieldCheck, ArrowRightLeft, Loader2, XCircle, CheckCircle2, X,
} from 'lucide-react'
import { get, post, patch, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatMB } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Switch } from '@/components/ui/Toggle'
import { Badge } from '@/components/ui/Badge'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import { EmptyState, ErrorState } from '@/components/ui/States'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { toast } from '@/components/ui/Toast'

const EMAIL_TABS = [
  ['mailboxes', 'Mailboxes', Inbox],
  ['forwarders', 'Forwarders', Forward],
  ['catchall', 'Catch-all', AtSign],
  ['spam', 'Spam filter', ShieldAlert],
  ['spam-entries', 'Spam Filters', ShieldBan],
  ['imap-migrate', 'Migrate', ArrowRightLeft],
  ['routing', 'Routing', Network],
  ['delivery', 'Delivery log', ScrollText],
]

// --- Mailboxes -----------------------------------------------------------

function MailboxesTab({ domain }) {
  const qc = useQueryClient()
  const key = ['mailboxes', domain]
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ local_part: '', password: '', quota_mb: 1024 })
  const [toDelete, setToDelete] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: key,
    queryFn: () => get(`/api/v1/mail/domains/${domain}/mailboxes`),
    enabled: !!domain,
  })

  const createMut = useMutation({
    mutationFn: (body) => post('/api/v1/mail/mailboxes', body),
    onSuccess: () => {
      toast.success('Mailbox created')
      qc.invalidateQueries({ queryKey: key })
      setCreateOpen(false)
      setForm({ local_part: '', password: '', quota_mb: 1024 })
    },
    onError: (e) => toast.error('Could not create mailbox', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (localPart) => del(`/api/v1/mail/domains/${domain}/mailboxes/${localPart}`),
    onSuccess: () => {
      toast.success('Mailbox deleted')
      qc.invalidateQueries({ queryKey: key })
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not delete mailbox', e.message),
  })

  const columns = [
    {
      key: 'local_part',
      header: 'Mailbox',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.local_part}@{domain}</span>,
    },
    {
      key: 'quota_mb',
      header: 'Quota',
      sortable: true,
      sortValue: (r) => r.quota_mb ?? 0,
      render: (r) => formatMB(r.quota_mb),
    },
    {
      key: 'active',
      header: 'Status',
      render: (r) => (r.active ? <Badge variant="success">Active</Badge> : <Badge variant="neutral">Inactive</Badge>),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (r) => (
        <Button variant="ghost" size="icon-sm" onClick={() => setToDelete(r.local_part)} aria-label="Delete mailbox">
          <Trash2 className="h-4 w-4 text-danger" />
        </Button>
      ),
    },
  ]

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">Mailboxes for <span className="font-medium text-foreground">{domain}</span></p>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Create mailbox
        </Button>
      </div>

      <DataTable
        columns={columns}
        data={data?.mailboxes}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search mailboxes…"
        pageSize={10}
        getRowKey={(r) => r.local_part}
        emptyTitle="No mailboxes yet"
        emptyDescription="Create a mailbox to start receiving mail on this domain."
        emptyIcon={Inbox}
        emptyAction={<Button size="sm" onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Create mailbox</Button>}
      />

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Create mailbox</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({
                domain,
                local_part: form.local_part,
                password: form.password,
                quota_mb: Number(form.quota_mb) || 1024,
              })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Mailbox" required hint={`The full address will be ${form.local_part || 'name'}@${domain}`}>
                <div className="flex items-center gap-2">
                  <Input
                    autoFocus
                    value={form.local_part}
                    onChange={(e) => setForm((f) => ({ ...f, local_part: e.target.value }))}
                    placeholder="john"
                    required
                  />
                  <span className="whitespace-nowrap text-sm text-muted-foreground">@{domain}</span>
                </div>
              </FormField>
              <FormField label="Password" required hint="Minimum 10 characters.">
                <Input
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                  placeholder="••••••••••"
                  required
                />
              </FormField>
              <FormField label="Quota (MB)" hint="Storage limit for this mailbox.">
                <Input
                  type="number"
                  min="1"
                  value={form.quota_mb}
                  onChange={(e) => setForm((f) => ({ ...f, quota_mb: e.target.value }))}
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending}>Create mailbox</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => !v && setToDelete(null)}
        title="Delete mailbox?"
        description={toDelete ? `${toDelete}@${domain} and all of its stored mail will be permanently removed.` : ''}
        confirmLabel="Delete mailbox"
        confirmationText={toDelete ? `${toDelete}@${domain}` : undefined}
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(toDelete)}
      />
    </div>
  )
}

// --- Forwarders ----------------------------------------------------------

function ForwardersTab({ username, domain }) {
  const qc = useQueryClient()
  const key = ['forwarders', username, domain]
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ local_part: '', destination: '' })
  const [toDelete, setToDelete] = useState(null)

  const base = `/api/v1/accounts/${username}/domains/${domain}/email/forwarders`

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: key,
    queryFn: () => get(base),
    enabled: !!username && !!domain,
  })

  const createMut = useMutation({
    mutationFn: (body) => post(base, body),
    onSuccess: () => {
      toast.success('Forwarder created')
      qc.invalidateQueries({ queryKey: key })
      setCreateOpen(false)
      setForm({ local_part: '', destination: '' })
    },
    onError: (e) => toast.error('Could not create forwarder', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (fwd) => del(base, { params: { local_part: fwd.local_part, destination: fwd.destination } }),
    onSuccess: () => {
      toast.success('Forwarder deleted')
      qc.invalidateQueries({ queryKey: key })
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not delete forwarder', e.message),
  })

  const columns = [
    {
      key: 'local_part',
      header: 'Address',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.local_part}@{domain}</span>,
    },
    {
      key: 'destination',
      header: 'Forwards to',
      sortable: true,
      searchable: true,
      render: (r) => <span className="text-foreground">{r.destination}</span>,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (r) => (
        <Button variant="ghost" size="icon-sm" onClick={() => setToDelete(r)} aria-label="Delete forwarder">
          <Trash2 className="h-4 w-4 text-danger" />
        </Button>
      ),
    },
  ]

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">Forward mail from an address on <span className="font-medium text-foreground">{domain}</span> to another mailbox.</p>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Add forwarder
        </Button>
      </div>

      <DataTable
        columns={columns}
        data={data?.forwards}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search forwarders…"
        pageSize={10}
        getRowKey={(r) => `${r.local_part}→${r.destination}`}
        emptyTitle="No forwarders yet"
        emptyDescription="Forward incoming mail to another address."
        emptyIcon={Forward}
        emptyAction={<Button size="sm" onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Add forwarder</Button>}
      />

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Add forwarder</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({ local_part: form.local_part, destination: form.destination })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="From address" required hint={`Mail sent to ${form.local_part || 'name'}@${domain}`}>
                <div className="flex items-center gap-2">
                  <Input
                    autoFocus
                    value={form.local_part}
                    onChange={(e) => setForm((f) => ({ ...f, local_part: e.target.value }))}
                    placeholder="sales"
                    required
                  />
                  <span className="whitespace-nowrap text-sm text-muted-foreground">@{domain}</span>
                </div>
              </FormField>
              <FormField label="Destination" required hint="Where the mail should be delivered.">
                <Input
                  type="email"
                  value={form.destination}
                  onChange={(e) => setForm((f) => ({ ...f, destination: e.target.value }))}
                  placeholder="you@example.com"
                  required
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending}>Add forwarder</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => !v && setToDelete(null)}
        title="Delete forwarder?"
        description={toDelete ? `Mail to ${toDelete.local_part}@${domain} will no longer be forwarded to ${toDelete.destination}.` : ''}
        confirmLabel="Delete forwarder"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(toDelete)}
      />
    </div>
  )
}

// --- Catch-all -----------------------------------------------------------

function CatchallTab({ username, domain }) {
  const qc = useQueryClient()
  const key = ['catchall', username, domain]
  const [destination, setDestination] = useState('')
  const [confirmOpen, setConfirmOpen] = useState(false)

  const base = `/api/v1/accounts/${username}/domains/${domain}/email/catchall`

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: key,
    queryFn: () => get(base),
    enabled: !!username && !!domain,
  })

  const current = data?.catchall || null

  useEffect(() => {
    setDestination(current?.destination || '')
  }, [current?.destination, domain])

  const setMut = useMutation({
    mutationFn: (body) => post(base, body),
    onSuccess: () => {
      toast.success('Catch-all saved')
      qc.invalidateQueries({ queryKey: key })
    },
    onError: (e) => toast.error('Could not save catch-all', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: () => del(base),
    onSuccess: () => {
      toast.success('Catch-all removed')
      qc.invalidateQueries({ queryKey: key })
      setConfirmOpen(false)
    },
    onError: (e) => toast.error('Could not remove catch-all', e.message),
  })

  if (isLoading) return <CenteredSpinner />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  return (
    <Card>
      <CardHeader>
        <CardTitle>Catch-all address</CardTitle>
        <CardDescription>
          Deliver any mail sent to an address that does not have its own mailbox on {domain} to a single destination.
        </CardDescription>
      </CardHeader>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setMut.mutate({ destination })
        }}
      >
        <CardContent>
          {current ? (
            <div className="mb-4 flex items-center gap-2 text-sm">
              <Badge variant="success">Active</Badge>
              <span className="text-muted-foreground">Currently catching to</span>
              <span className="font-medium text-foreground">{current.destination}</span>
            </div>
          ) : (
            <div className="mb-4 flex items-center gap-2 text-sm">
              <Badge variant="neutral">Not configured</Badge>
              <span className="text-muted-foreground">No catch-all is set for this domain.</span>
            </div>
          )}
          <FormField label="Destination" required hint="Unmatched mail for this domain is delivered here.">
            <Input
              type="email"
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
              placeholder="catchall@example.com"
              required
            />
          </FormField>
        </CardContent>
        <CardFooter className="justify-end gap-3">
          {current && (
            <Button type="button" variant="danger" onClick={() => setConfirmOpen(true)}>
              <Trash2 className="h-4 w-4" /> Remove
            </Button>
          )}
          <Button type="submit" loading={setMut.isPending}>Save catch-all</Button>
        </CardFooter>
      </form>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Remove catch-all?"
        description={`Mail sent to unknown addresses at ${domain} will be rejected instead of collected.`}
        confirmLabel="Remove catch-all"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate()}
      />
    </Card>
  )
}

// --- Spam filter ---------------------------------------------------------

function SpamTab({ username, domain }) {
  const qc = useQueryClient()
  const key = ['spam-filter', username, domain]
  const [enabled, setEnabled] = useState(true)
  const [threshold, setThreshold] = useState('')

  const base = `/api/v1/accounts/${username}/domains/${domain}/email/spam-filter`

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: key,
    queryFn: () => get(base),
    enabled: !!username && !!domain,
  })

  useEffect(() => {
    if (data) {
      setEnabled(!!data.enabled)
      setThreshold(data.threshold === null || data.threshold === undefined ? '' : String(data.threshold))
    }
  }, [data])

  const saveMut = useMutation({
    mutationFn: (body) => patch(base, body),
    onSuccess: () => {
      toast.success('Spam filter updated')
      qc.invalidateQueries({ queryKey: key })
    },
    onError: (e) => toast.error('Could not update spam filter', e.message),
  })

  if (isLoading) return <CenteredSpinner />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  const effective = data?.effective_threshold

  return (
    <Card>
      <CardHeader>
        <CardTitle>Spam filter</CardTitle>
        <CardDescription>
          Score incoming mail for {domain} and quarantine anything above the threshold. A lower score is more aggressive.
        </CardDescription>
      </CardHeader>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          saveMut.mutate({
            enabled,
            threshold: threshold === '' ? null : Number(threshold),
          })
        }}
      >
        <CardContent className="space-y-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-sm font-medium text-foreground">Filter enabled</div>
              <div className="text-xs text-muted-foreground">Turn spam scoring on or off for this domain.</div>
            </div>
            <Switch checked={enabled} onCheckedChange={setEnabled} />
          </div>
          <FormField
            label="Score threshold"
            hint={
              effective !== null && effective !== undefined
                ? `Leave blank to use the server default (${effective}).`
                : 'Leave blank to use the server default.'
            }
          >
            <Input
              type="number"
              step="0.1"
              min="0"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              placeholder={effective !== null && effective !== undefined ? String(effective) : '5.0'}
              disabled={!enabled}
            />
          </FormField>
        </CardContent>
        <CardFooter className="justify-end">
          <Button type="submit" loading={saveMut.isPending}>Save changes</Button>
        </CardFooter>
      </form>
    </Card>
  )
}

// --- Routing (Phase 8 feature 6) -----------------------------------------

const ROUTING_MODES = [
  { value: 'local', label: 'Local', desc: 'This server accepts and delivers mail for the domain to its mailboxes here.' },
  { value: 'remote', label: 'Remote', desc: 'This server stops accepting mail for the domain — it flows to the external MX in DNS.' },
  { value: 'backup', label: 'Backup MX', desc: 'This server queues mail and relays it to the primary (external) MX.' },
]

function RoutingTab({ username, domain }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/domains/${domain}/email/routing`
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['email-routing', username, domain],
    queryFn: () => get(base),
    enabled: !!username && !!domain,
  })
  const mut = useMutation({
    mutationFn: (mode) => patch(base, { mode }),
    onSuccess: (res) => { toast.success('Email routing updated', `${domain} → ${res.mode}`); qc.invalidateQueries({ queryKey: ['email-routing', username, domain] }) },
    onError: (e) => toast.error('Could not update routing', e.message),
  })

  if (isLoading) return <CenteredSpinner />
  if (error) return <ErrorState error={error} onRetry={refetch} />
  const current = data?.mode || 'local'

  return (
    <Card>
      <CardHeader>
        <CardTitle>Mail routing for {domain}</CardTitle>
        <CardDescription>Where mail for this domain is accepted and delivered.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {ROUTING_MODES.map((m) => (
          <label key={m.value}
            className={`flex cursor-pointer items-start gap-3 rounded-card border px-4 py-3 ${current === m.value ? 'border-accent bg-accent/5' : 'border-border'}`}>
            <input type="radio" name="routing" className="mt-1" checked={current === m.value}
              disabled={mut.isPending} onChange={() => mut.mutate(m.value)} />
            <div>
              <div className="text-sm font-medium text-foreground">{m.label}</div>
              <div className="text-xs text-muted-foreground">{m.desc}</div>
            </div>
          </label>
        ))}
      </CardContent>
    </Card>
  )
}

// --- Delivery log (Phase 8 feature 5) ------------------------------------

function DeliveryLogTab({ username }) {
  const [search, setSearch] = useState('')
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['email-delivery-log', username, search],
    queryFn: () => get(`/api/v1/accounts/${username}/email/delivery-log`, { params: { search } }),
    enabled: !!username,
  })

  const STATUS_VARIANT = { sent: 'success', bounced: 'danger', deferred: 'warning', rejected: 'danger', expired: 'danger' }
  const columns = [
    { key: 'timestamp', header: 'Time', render: (r) => <span className="whitespace-nowrap font-mono text-xs">{r.timestamp}</span> },
    { key: 'from', header: 'From', searchable: true, render: (r) => <span className="break-all font-mono text-xs">{r.from || '—'}</span> },
    { key: 'to', header: 'To', searchable: true, render: (r) => <span className="break-all font-mono text-xs">{r.to}</span> },
    { key: 'status', header: 'Status', render: (r) => <Badge variant={STATUS_VARIANT[r.status] || 'neutral'}>{r.status}</Badge> },
    { key: 'reason', header: 'Reason', render: (r) => <span className="break-all text-xs text-muted-foreground">{r.reason}</span> },
  ]

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <div>
          <CardTitle>Delivery log</CardTitle>
          <CardDescription>The last {500} Postfix delivery events touching your domains.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search address/status…" className="w-56" />
          <Button variant="secondary" onClick={() => refetch()} loading={isFetching}>Refresh</Button>
        </div>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={columns}
          data={data?.entries}
          loading={isLoading}
          error={error}
          onRetry={refetch}
          getRowKey={(r, i) => `${r.queue_id || 'nq'}-${r.to}-${i}`}
          pageSize={25}
          emptyTitle="No delivery events"
          emptyDescription="No recent Postfix activity for this account's domains."
          emptyIcon={Mail}
        />
      </CardContent>
    </Card>
  )
}

// --- Page ----------------------------------------------------------------

// --- Per-mailbox spam filters (missing-features batch, goal feature 5) ---

function MailboxSpamFiltersTab({ username, domain }) {
  const qc = useQueryClient()
  const mailboxesQ = useQuery({
    queryKey: ['mailboxes', domain],
    queryFn: () => get(`/api/v1/mail/domains/${domain}/mailboxes`),
    enabled: !!domain,
  })
  const mailboxes = mailboxesQ.data?.mailboxes || []
  const [mailbox, setMailbox] = useState('')
  const [pattern, setPattern] = useState('')
  const [importOpen, setImportOpen] = useState(false)
  const [importKind, setImportKind] = useState('blacklist')
  const [importText, setImportText] = useState('')

  useEffect(() => {
    if (!mailbox && mailboxes.length) setMailbox(mailboxes[0].local_part)
  }, [mailboxes, mailbox])

  const base = `/api/v1/accounts/${username}/email/${mailbox}/spam-filters`
  const key = ['spam-filter-entries', username, mailbox, domain]
  const entriesQ = useQuery({
    queryKey: key,
    queryFn: () => get(base, { params: { domain } }),
    enabled: !!username && !!mailbox && !!domain,
  })
  const invalidate = () => qc.invalidateQueries({ queryKey: key })

  const addMut = useMutation({
    mutationFn: (kind) => post(base, { domain, kind, pattern: pattern.trim() }),
    onSuccess: (_r, kind) => { toast.success(`Added to ${kind}`); setPattern(''); invalidate() },
    onError: (e) => toast.error('Could not add entry', e.message),
  })
  const deleteMut = useMutation({
    mutationFn: (id) => del(base, { data: { id, domain } }),
    onSuccess: () => { toast.success('Entry removed'); invalidate() },
    onError: (e) => toast.error('Could not remove entry', e.message),
  })
  const importMut = useMutation({
    mutationFn: () => post(`${base}/import`, { domain, kind: importKind, text: importText }),
    onSuccess: (res) => {
      toast.success('Import finished', `${res.added.length} added, ${res.errors.length} skipped`)
      setImportOpen(false); setImportText(''); invalidate()
    },
    onError: (e) => toast.error('Could not import list', e.message),
  })

  const entries = entriesQ.data?.entries || []
  const blacklist = entries.filter((e) => e.kind === 'blacklist')
  const whitelist = entries.filter((e) => e.kind === 'whitelist')

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <div>
          <CardTitle className="flex items-center gap-2"><ShieldBan className="h-4 w-4" /> Per-mailbox spam filters</CardTitle>
          <CardDescription>Always reject mail from a blacklisted address/domain, or always deliver mail from a whitelisted one — checked before spam scoring.</CardDescription>
        </div>
        {mailboxes.length > 0 && (
          <Select value={mailbox} onChange={(e) => setMailbox(e.target.value)} className="w-56">
            {mailboxes.map((m) => <option key={m.local_part} value={m.local_part}>{m.local_part}@{domain}</option>)}
          </Select>
        )}
      </CardHeader>
      <CardContent className="space-y-6">
        {mailboxes.length === 0 ? (
          <p className="text-sm text-muted-foreground">Create a mailbox first — spam filters are configured per mailbox.</p>
        ) : entriesQ.isLoading ? (
          <CenteredSpinner />
        ) : entriesQ.error ? (
          <ErrorState error={entriesQ.error} onRetry={entriesQ.refetch} />
        ) : (
          <>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="sender@example.com or example.com" className="flex-1" />
              <Button variant="outline" disabled={!pattern.trim()} loading={addMut.isPending} onClick={() => addMut.mutate('whitelist')}>
                <ShieldCheck className="h-4 w-4" /> Whitelist
              </Button>
              <Button variant="outline" disabled={!pattern.trim()} loading={addMut.isPending} onClick={() => addMut.mutate('blacklist')}>
                <ShieldBan className="h-4 w-4" /> Blacklist
              </Button>
              <Button variant="ghost" onClick={() => setImportOpen(true)}>Import list</Button>
            </div>
            <div className="grid gap-6 md:grid-cols-2">
              <div>
                <h4 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-foreground"><ShieldBan className="h-3.5 w-3.5 text-danger" /> Blacklist ({blacklist.length})</h4>
                {blacklist.length === 0 ? <p className="text-xs text-muted-foreground">No blacklisted senders.</p> : (
                  <ul className="space-y-1.5">
                    {blacklist.map((e) => (
                      <li key={e.id} className="flex items-center justify-between rounded-btn border border-border px-3 py-1.5 text-sm">
                        <span className="font-mono text-xs">{e.pattern}</span>
                        <button type="button" onClick={() => deleteMut.mutate(e.id)} className="rounded-btn p-1 text-muted-foreground hover:text-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={`Delete allow-list pattern ${e.pattern}`}><X className="h-3.5 w-3.5" /></button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <h4 className="mb-2 flex items-center gap-1.5 text-sm font-medium text-foreground"><ShieldCheck className="h-3.5 w-3.5 text-success" /> Whitelist ({whitelist.length})</h4>
                {whitelist.length === 0 ? <p className="text-xs text-muted-foreground">No whitelisted senders.</p> : (
                  <ul className="space-y-1.5">
                    {whitelist.map((e) => (
                      <li key={e.id} className="flex items-center justify-between rounded-btn border border-border px-3 py-1.5 text-sm">
                        <span className="font-mono text-xs">{e.pattern}</span>
                        <button type="button" onClick={() => deleteMut.mutate(e.id)} className="rounded-btn p-1 text-muted-foreground hover:text-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={`Delete block-list pattern ${e.pattern}`}><X className="h-3.5 w-3.5" /></button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </>
        )}
      </CardContent>
      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Import list</DialogTitle></DialogHeader>
          <DialogBody className="space-y-3">
            <FormField label="List type">
              <Select value={importKind} onChange={(e) => setImportKind(e.target.value)}>
                <option value="blacklist">Blacklist</option>
                <option value="whitelist">Whitelist</option>
              </Select>
            </FormField>
            <FormField label="Addresses / domains" hint="One per line. Lines starting with # are ignored.">
              <textarea rows={10} className="w-full rounded-btn border border-border bg-background px-3 py-2 font-mono text-xs"
                value={importText} onChange={(e) => setImportText(e.target.value)} placeholder={'spammer@evil.com\nbad-domain.com'} />
            </FormField>
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setImportOpen(false)}>Cancel</Button>
            <Button loading={importMut.isPending} disabled={!importText.trim()} onClick={() => importMut.mutate()}>Import</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}

// --- IMAPSync migrations (missing-features batch, goal feature 1) --------

const IMAP_JOB_STATUS_VARIANT = {
  pending: 'neutral', connecting: 'info', running: 'info',
  completed: 'success', failed: 'danger', cancelled: 'neutral',
}

function ImapMigrateTab({ username, domain }) {
  const qc = useQueryClient()
  const mailboxesQ = useQuery({
    queryKey: ['mailboxes', domain],
    queryFn: () => get(`/api/v1/mail/domains/${domain}/mailboxes`),
    enabled: !!domain,
  })
  const mailboxes = mailboxesQ.data?.mailboxes || []
  const [mailbox, setMailbox] = useState('')
  useEffect(() => {
    if (!mailbox && mailboxes.length) setMailbox(mailboxes[0].local_part)
  }, [mailboxes, mailbox])

  const [form, setForm] = useState({
    source_host: '', source_port: 993, source_ssl: true,
    source_email: '', source_password: '', dest_password: '',
  })
  const [folders, setFolders] = useState(null) // null = not listed yet, [] = listed, all synced
  const [selectedFolders, setSelectedFolders] = useState(new Set())

  const jobsKey = ['imap-migrations', username]
  const jobsQ = useQuery({
    queryKey: jobsKey,
    queryFn: () => get(`/api/v1/accounts/${username}/email/imap-migrate`),
    enabled: !!username,
    refetchInterval: (q) => (q.state.data?.jobs || []).some((j) => ['pending', 'connecting', 'running'].includes(j.status)) ? 3000 : false,
  })
  const jobs = jobsQ.data?.jobs || []

  const listMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${username}/email/imap-migrate/list-folders`, {
      source_host: form.source_host, source_port: Number(form.source_port), source_ssl: form.source_ssl,
      source_email: form.source_email, source_password: form.source_password,
    }),
    onSuccess: (res) => { setFolders(res.folders); setSelectedFolders(new Set(res.folders)); toast.success(`Found ${res.folders.length} folder(s)`) },
    onError: (e) => toast.error('Could not connect to source server', e.message),
  })

  const startMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${username}/email/imap-migrate`, {
      domain, local_part: mailbox,
      source_host: form.source_host, source_port: Number(form.source_port), source_ssl: form.source_ssl,
      source_email: form.source_email, source_password: form.source_password, dest_password: form.dest_password,
      folders: folders ? Array.from(selectedFolders) : [],
    }),
    onSuccess: () => {
      toast.success('Migration started', 'Progress will appear below.')
      setForm({ source_host: '', source_port: 993, source_ssl: true, source_email: '', source_password: '', dest_password: '' })
      setFolders(null); setSelectedFolders(new Set())
      qc.invalidateQueries({ queryKey: jobsKey })
    },
    onError: (e) => toast.error('Could not start migration', e.message),
  })

  const cancelMut = useMutation({
    mutationFn: (jobId) => post(`/api/v1/accounts/${username}/email/imap-migrate/${jobId}/cancel`, {}),
    onSuccess: () => { toast.success('Migration cancelled'); qc.invalidateQueries({ queryKey: jobsKey }) },
    onError: (e) => toast.error('Could not cancel migration', e.message),
  })

  const canStart = mailbox && form.source_host && form.source_email && form.source_password && form.dest_password

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><ArrowRightLeft className="h-4 w-4" /> Migrate mail from another server</CardTitle>
          <CardDescription>
            Copy mail from an external IMAP account into one of your Boron mailboxes. Credentials are used only
            for this migration and are never stored or logged.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {mailboxes.length === 0 ? (
            <p className="text-sm text-muted-foreground">Create a mailbox first — that's the migration destination.</p>
          ) : (
            <>
              <FormField label="Destination mailbox">
                <Select value={mailbox} onChange={(e) => setMailbox(e.target.value)}>
                  {mailboxes.map((m) => <option key={m.local_part} value={m.local_part}>{m.local_part}@{domain}</option>)}
                </Select>
              </FormField>
              <FormField label="This mailbox's own (Boron) password" hint="Needed so the migration can log in and deliver mail here.">
                <Input type="password" value={form.dest_password} onChange={(e) => setForm((f) => ({ ...f, dest_password: e.target.value }))} />
              </FormField>
              <div className="border-t border-border pt-4">
                <p className="mb-3 text-sm font-medium text-foreground">Source server</p>
                <div className="grid gap-4 sm:grid-cols-2">
                  <FormField label="Source IMAP host">
                    <Input value={form.source_host} onChange={(e) => setForm((f) => ({ ...f, source_host: e.target.value }))} placeholder="imap.oldprovider.com" />
                  </FormField>
                  <FormField label="Port">
                    <Input type="number" value={form.source_port} onChange={(e) => setForm((f) => ({ ...f, source_port: e.target.value }))} />
                  </FormField>
                  <FormField label="Source email">
                    <Input type="email" value={form.source_email} onChange={(e) => setForm((f) => ({ ...f, source_email: e.target.value }))} />
                  </FormField>
                  <FormField label="Source password">
                    <Input type="password" value={form.source_password} onChange={(e) => setForm((f) => ({ ...f, source_password: e.target.value }))} />
                  </FormField>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <Switch checked={form.source_ssl} onCheckedChange={(v) => setForm((f) => ({ ...f, source_ssl: v }))} />
                  <span className="text-sm text-muted-foreground">Use SSL/TLS to connect to the source server</span>
                </div>
              </div>
              {folders !== null && (
                <FormField label={`Folders to migrate (${selectedFolders.size}/${folders.length} selected)`}>
                  <div className="max-h-48 space-y-1 overflow-y-auto rounded-btn border border-border p-2">
                    {folders.map((f) => (
                      <label key={f} className="flex items-center gap-2 text-sm">
                        <input type="checkbox" checked={selectedFolders.has(f)}
                          onChange={(e) => setSelectedFolders((prev) => {
                            const next = new Set(prev)
                            e.target.checked ? next.add(f) : next.delete(f)
                            return next
                          })} />
                        <span className="font-mono text-xs">{f}</span>
                      </label>
                    ))}
                  </div>
                </FormField>
              )}
            </>
          )}
        </CardContent>
        {mailboxes.length > 0 && (
          <CardFooter className="flex flex-wrap gap-2">
            <Button variant="outline" loading={listMut.isPending}
              disabled={!form.source_host || !form.source_email || !form.source_password}
              onClick={() => listMut.mutate()}>
              List source folders
            </Button>
            <Button loading={startMut.isPending} disabled={!canStart} onClick={() => startMut.mutate()}>
              <ArrowRightLeft className="h-4 w-4" /> Start migration
            </Button>
          </CardFooter>
        )}
      </Card>

      <Card>
        <CardHeader><CardTitle>Migration jobs</CardTitle></CardHeader>
        <CardContent>
          {jobsQ.isLoading ? <CenteredSpinner /> : jobs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No migrations yet.</p>
          ) : (
            <div className="space-y-3">
              {jobs.map((job) => (
                <div key={job.id} className="rounded-card border border-border p-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      {['pending', 'connecting', 'running'].includes(job.status) && <Loader2 className="h-3.5 w-3.5 animate-spin text-info" />}
                      <span className="text-sm font-medium text-foreground">{job.mailbox}</span>
                      <span className="text-xs text-muted-foreground">from {job.source_host}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant={IMAP_JOB_STATUS_VARIANT[job.status] || 'neutral'}>{job.status}</Badge>
                      {['pending', 'connecting', 'running'].includes(job.status) && (
                        <button type="button" onClick={() => cancelMut.mutate(job.id)} className="rounded-btn p-1 text-muted-foreground hover:text-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" title="Cancel" aria-label={`Cancel migration job ${job.id}`}>
                          <XCircle className="h-4 w-4" />
                        </button>
                      )}
                    </div>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{job.progress_message}</p>
                  {job.folders_total > 0 && (
                    <div className="mt-2">
                      <ProgressBarInline value={job.folders_done} max={job.folders_total} />
                      <p className="mt-1 text-xs text-muted-foreground">{job.folders_done}/{job.folders_total} folders, {job.messages_done} messages copied</p>
                    </div>
                  )}
                  {job.results?.length > 0 && (
                    <ul className="mt-2 space-y-0.5">
                      {job.results.map((r) => (
                        <li key={r.folder} className="flex items-center gap-1.5 text-xs">
                          {r.status === 'ok' ? <CheckCircle2 className="h-3 w-3 text-success" /> : <XCircle className="h-3 w-3 text-danger" />}
                          <span className="font-mono">{r.folder}</span>
                          <span className="text-muted-foreground">{r.status === 'ok' ? `${r.messages} messages` : r.detail}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {job.error && <p className="mt-1 text-xs text-danger">{job.error}</p>}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function ProgressBarInline({ value, max }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${pct}%` }} />
    </div>
  )
}

export default function Email() {
  const [searchParams, setSearchParams] = useSearchParams()
  const emailTabs = new Set(EMAIL_TABS.map(([value]) => value))
  const requestedTab = searchParams.get('tab')
  const activeTab = emailTabs.has(requestedTab) ? requestedTab : 'mailboxes'
  const username = useAccountUsername()
  const [domain, setDomain] = useState('')

  const domainsQ = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })

  const domains = domainsQ.data?.domains || []

  useEffect(() => {
    if (!domain && domains.length) setDomain(domains[0].domain)
  }, [domains, domain])

  return (
    <div>
      <PageHeader title="Email" description="Manage mailboxes, forwarders, catch-all delivery, and spam filtering." icon={Mail}>
        {domains.length > 0 && (
          <div className="flex items-center gap-2">
            <AtSign className="h-4 w-4 text-muted-foreground" />
            <Select value={domain} onChange={(e) => setDomain(e.target.value)} className="w-56">
              {domains.map((d) => (
                <option key={d.domain} value={d.domain}>{d.domain}</option>
              ))}
            </Select>
          </div>
        )}
      </PageHeader>

      {domainsQ.isLoading ? (
        <CenteredSpinner />
      ) : domainsQ.error ? (
        <ErrorState error={domainsQ.error} onRetry={domainsQ.refetch} />
      ) : domains.length === 0 ? (
        <EmptyState
          icon={Mail}
          title="No domains yet"
          description="Add a domain to your account before you can manage its email."
        />
      ) : !domain ? (
        <CenteredSpinner />
      ) : (
        <Tabs value={activeTab} key={domain} onValueChange={(tab) => setSearchParams((prev) => {
          const next = new URLSearchParams(prev)
          if (tab === 'mailboxes') next.delete('tab')
          else next.set('tab', tab)
          return next
        })}>
          <Select
            value={activeTab}
            onChange={(event) => setSearchParams((prev) => {
              const next = new URLSearchParams(prev)
              if (event.target.value === 'mailboxes') next.delete('tab')
              else next.set('tab', event.target.value)
              return next
            })}
            aria-label="Email section"
            className="mb-4 sm:hidden"
          >
            {EMAIL_TABS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </Select>
          <TabsList className="hidden sm:flex">
            {EMAIL_TABS.map(([value, label, Icon]) => (
              <TabsTrigger key={value} value={value}><Icon className="h-4 w-4" /> {label}</TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="mailboxes">
            <MailboxesTab domain={domain} />
          </TabsContent>
          <TabsContent value="forwarders">
            <ForwardersTab username={username} domain={domain} />
          </TabsContent>
          <TabsContent value="catchall">
            <CatchallTab username={username} domain={domain} />
          </TabsContent>
          <TabsContent value="spam">
            <SpamTab username={username} domain={domain} />
          </TabsContent>
          <TabsContent value="spam-entries">
            <MailboxSpamFiltersTab username={username} domain={domain} />
          </TabsContent>
          <TabsContent value="imap-migrate">
            <ImapMigrateTab username={username} domain={domain} />
          </TabsContent>
          <TabsContent value="routing">
            <RoutingTab username={username} domain={domain} />
          </TabsContent>
          <TabsContent value="delivery">
            <DeliveryLogTab username={username} />
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}
