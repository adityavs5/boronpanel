import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Mail, Plus, Trash2, Inbox, Forward, ShieldAlert, AtSign, Network, ScrollText,
  ShieldBan, ShieldCheck, ArrowRightLeft, Loader2, XCircle, CheckCircle2, X, ExternalLink,
  CirclePause, CirclePlay, Settings2, LogIn,
} from 'lucide-react'
import { get, post, patch, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { useDomainContext } from '@/hooks/useDomainContext'
import { formatMB } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Switch } from '@/components/ui/Toggle'
import { Badge } from '@/components/ui/Badge'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import { EmptyState, ErrorState } from '@/components/ui/States'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { toast } from '@/components/ui/Toast'

const EMAIL_MODES = {
  accounts: {
    title: 'Email Accounts',
    description: 'Create mailboxes and manage login, passwords, quotas, and account access.',
    sections: [['mailboxes', 'Email accounts', Inbox]],
  },
  webmail: {
    title: 'Webmail',
    description: 'Open a mailbox securely without entering its password again.',
    sections: [['webmail', 'Webmail login', LogIn]],
  },
  settings: {
    title: 'Email Settings',
    description: 'Configure forwarding and delivery behavior for the selected domain.',
    sections: [
      ['forwarders', 'Forwarders', Forward],
      ['catchall', 'Catch-all', AtSign],
      ['routing', 'Mail routing', Network],
      ['delivery', 'Delivery log', ScrollText],
    ],
  },
  spam: {
    title: 'Spam Filters',
    description: 'Control spam scoring and mailbox-level allow and block lists.',
    sections: [
      ['spam', 'Spam scoring', ShieldAlert],
      ['spam-entries', 'Allow & block lists', ShieldBan],
    ],
  },
  migration: {
    title: 'IMAP Migration',
    description: 'Copy messages from an existing mail server into a Boron mailbox.',
    sections: [['imap-migrate', 'IMAP migration', ArrowRightLeft]],
  },
}

function useWebmailLaunch(username, domain) {
  const [launching, setLaunching] = useState('')
  const openWebmail = async (row) => {
    if (!row.active) return
    const address = `${row.local_part}@${domain}`
    const target = `boron-webmail-${Date.now()}`
    const tab = window.open('', target)
    if (tab) tab.opener = null
    setLaunching(address)
    try {
      const launch = await post(`/api/v1/accounts/${encodeURIComponent(username)}/email/${encodeURIComponent(row.local_part)}/webmail-session`, { domain })
      const form = document.createElement('form')
      form.method = 'POST'
      form.action = `${launch.url.replace(/\/$/, '')}/?_task=login&_action=login`
      form.target = tab ? target : '_blank'
      for (const [name, value] of Object.entries({ _task: 'login', _action: 'login', _user: address, _pass: 'boron-sso', _boron_token: launch.token })) {
        const input = document.createElement('input')
        input.type = 'hidden'; input.name = name; input.value = value; form.appendChild(input)
      }
      document.body.appendChild(form); form.submit(); form.remove()
    } catch (error) {
      if (tab) tab.close()
      toast.error('Could not open webmail', error.message)
    } finally { setLaunching('') }
  }
  return { launching, openWebmail }
}

// --- Mailboxes -----------------------------------------------------------

function MailboxesTab({ domain }) {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const key = ['mailboxes', domain]
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ local_part: '', password: '', quota_mb: 1024 })
  const [toDelete, setToDelete] = useState(null)
  const [selected, setSelected] = useState(null)
  const [password, setPassword] = useState('')
  const [quotaGb, setQuotaGb] = useState('1')
  useEffect(() => { if (selected) setQuotaGb(String(selected.quota_mb / 1024)) }, [selected])
  const quotaMut = useMutation({ mutationFn: () => patch(`/api/v1/mail/domains/${encodeURIComponent(domain)}/mailboxes/${encodeURIComponent(selected.local_part)}/quota`, { quota_mb: Math.round(Number(quotaGb) * 1024) }), onSuccess: result => { toast.success('Mailbox quota updated'); setSelected(current => ({ ...current, quota_mb: result.quota_mb })); qc.invalidateQueries({ queryKey: key }) }, onError: error => toast.error('Could not update quota', error.message) })
  const { launching, openWebmail } = useWebmailLaunch(username, domain)
  useEffect(() => { setSelected(null); setPassword('') }, [domain, username])
  const passwordMut = useMutation({
    mutationFn: () => patch(`/api/v1/accounts/${encodeURIComponent(username)}/email/${encodeURIComponent(selected.local_part)}/password`, {domain, password}),
    onSuccess: () => { toast.success('Mailbox password updated'); setPassword('') },
    onError: error => toast.error('Could not update password', error.message),
  })

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

  const activeMut = useMutation({
    mutationFn: (row) => patch(
      `/api/v1/mail/domains/${encodeURIComponent(domain)}/mailboxes/${encodeURIComponent(row.local_part)}`,
      { active: !row.active },
    ),
    onSuccess: (result) => {
      toast.success(result.active ? 'Mailbox unsuspended' : 'Mailbox suspended')
      qc.invalidateQueries({ queryKey: key })
      setSelected(current => current?.local_part === result.local_part ? { ...current, active: result.active } : current)
    },
    onError: (error) => toast.error('Could not update mailbox status', error.message),
  })

  const columns = [
    {
      key: 'local_part',
      header: 'Mailbox',
      sortable: true,
      searchable: true,
      render: (r) => <button className="font-medium text-accent-600 dark:text-accent-300 hover:underline" onClick={() => setSelected(r)}>{r.local_part}@{domain}</button>,
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
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button variant="primary" size="sm" disabled={!r.active} loading={launching === `${r.local_part}@${domain}`} onClick={event => { event.stopPropagation(); openWebmail(r) }}><ExternalLink className="h-4 w-4" /> Log in</Button>
          <Button variant="secondary" size="sm" onClick={event => { event.stopPropagation(); setSelected(r) }}><Settings2 className="h-4 w-4" /> Manage</Button>
          <Button variant="secondary" size="sm" loading={activeMut.isPending && activeMut.variables?.local_part === r.local_part} onClick={event => { event.stopPropagation(); activeMut.mutate(r) }}>
            {r.active ? <CirclePause className="h-4 w-4" /> : <CirclePlay className="h-4 w-4" />} {r.active ? 'Suspend' : 'Unsuspend'}
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={event => { event.stopPropagation(); setToDelete(r.local_part) }} aria-label={`Delete ${r.local_part}@${domain}`}>
            <Trash2 className="h-4 w-4 text-danger" />
          </Button>
        </div>
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
        onRowClick={setSelected}
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

      <Dialog open={!!selected} onOpenChange={open => { if (!open && !passwordMut.isPending) { setSelected(null); setPassword('') } }}>
        <DialogContent size="sm"><DialogHeader><DialogTitle>Manage mailbox</DialogTitle></DialogHeader>
          <form onSubmit={event => { event.preventDefault(); if (password.length >= 12 && !passwordMut.isPending) passwordMut.mutate() }}>
            <DialogBody className="space-y-4"><p className="break-all font-medium">{selected?.local_part}@{domain}</p>
              <p className="text-sm text-muted-foreground">Quota: {formatMB(selected?.quota_mb)} · {selected?.active ? 'Active' : 'Inactive'}</p>
              <FormField label="Mailbox quota (GB)" hint="This is the mailbox storage limit. Updating it does not delete mail."><div className="flex items-center gap-2"><Input type="number" min={1 / 1024} max="100" step={1 / 1024} value={quotaGb} onChange={event => setQuotaGb(event.target.value)} /><Button type="button" variant="secondary" loading={quotaMut.isPending} disabled={!quotaGb || Number(quotaGb) <= 0 || Number(quotaGb) > 100} onClick={() => quotaMut.mutate()}>Update quota</Button></div></FormField>
              <FormField label="New mailbox password" required hint="Update your email clients after changing this password."><Input type="password" autoComplete="new-password" minLength={12} required value={password} disabled={passwordMut.isPending} onChange={event => setPassword(event.target.value)}/></FormField>
            </DialogBody><DialogFooter><Button type="button" variant="secondary" disabled={passwordMut.isPending} onClick={() => { setSelected(null); setPassword('') }}>Done</Button><Button type="submit" loading={passwordMut.isPending} disabled={password.length < 12}>Update password</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
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
    { key: 'source_user', header: 'Source', render: (r) => r.source_user ? <span className="font-mono text-xs">Local user: {r.source_user}</span> : <span className="text-muted-foreground">SMTP</span> },
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
  pending: 'neutral', connecting: 'info', running: 'info', cancelling: 'info',
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
    refetchInterval: (q) => (q.state.data?.jobs || []).some((j) => ['pending', 'connecting', 'running', 'cancelling'].includes(j.status)) ? 3000 : false,
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
                <p className="mt-3 text-sm text-muted-foreground">A verified TLS connection is required. Use port 993 for IMAPS or port 143 for STARTTLS.</p>
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
                      {['pending', 'connecting', 'running', 'cancelling'].includes(job.status) && <Loader2 className="h-3.5 w-3.5 animate-spin text-info" />}
                      <span className="text-sm font-medium text-foreground">{job.mailbox}</span>
                      <span className="text-xs text-muted-foreground">from {job.source_host}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant={IMAP_JOB_STATUS_VARIANT[job.status] || 'neutral'}>{job.status}</Badge>
                      {['pending', 'connecting', 'running', 'cancelling'].includes(job.status) && (
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

function WebmailTab({ username, domain }) {
  const { launching, openWebmail } = useWebmailLaunch(username, domain)
  const mailboxes = useQuery({
    queryKey: ['mailboxes', domain],
    queryFn: () => get(`/api/v1/mail/domains/${domain}/mailboxes`),
    enabled: !!domain,
  })
  const columns = [
    {
      key: 'local_part', header: 'Email account', sortable: true, searchable: true,
      render: row => <span className="font-medium text-foreground">{row.local_part}@{domain}</span>,
    },
    {
      key: 'active', header: 'Status',
      render: row => row.active ? <Badge variant="success">Active</Badge> : <Badge variant="neutral">Suspended</Badge>,
    },
    {
      key: 'actions', header: '', align: 'right',
      render: row => <Button size="sm" disabled={!row.active} loading={launching === `${row.local_part}@${domain}`} onClick={() => openWebmail(row)}><LogIn className="h-4 w-4" /> Log in to webmail</Button>,
    },
  ]
  return <DataTable columns={columns} data={mailboxes.data?.mailboxes} loading={mailboxes.isLoading} error={mailboxes.error} onRetry={mailboxes.refetch} filterable searchPlaceholder="Search email accounts…" pageSize={10} getRowKey={row => row.local_part} emptyTitle="No email accounts yet" emptyDescription="Create an email account before opening webmail." emptyIcon={Inbox} />
}

function EmailSectionPicker({ sections, active, onChange }) {
  if (sections.length < 2) return null
  return (
    <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Email tools">
      {sections.map(([value, label, Icon], index) => (
        <button key={value} type="button" aria-pressed={active === value} onClick={() => onChange(value)} className={`flex min-h-20 items-center gap-3 rounded-btn border px-4 py-3 text-left transition-colors ${active === value ? 'border-accent bg-accent-50 text-accent-700 dark:bg-accent-950 dark:text-accent-200' : 'border-border bg-surface text-foreground hover:bg-muted'}`}>
          <span className={`tool-icon tone-${['sky', 'green', 'violet', 'amber'][index % 4]}`}><Icon className="h-6 w-6" /></span>
          <span><strong className="block text-sm">{label}</strong><small className="text-xs text-muted-foreground">Open settings</small></span>
        </button>
      ))}
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

export default function Email({ mode = 'accounts' }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const effectiveMode = searchParams.get('webmail') === '1' ? 'webmail' : mode
  const config = EMAIL_MODES[effectiveMode] || EMAIL_MODES.accounts
  const allowedSections = new Set(config.sections.map(([value]) => value))
  const requestedTab = searchParams.get('tab')
  const activeSection = allowedSections.has(requestedTab) ? requestedTab : config.sections[0][0]
  const username = useAccountUsername()

  const domainsQ = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })
  const domains = domainsQ.data?.domains || []
  const [domain, setDomain] = useDomainContext(username, domains)

  const selectSection = (section) => setSearchParams((previous) => {
    const next = new URLSearchParams(previous)
    next.delete('webmail')
    if (section === config.sections[0][0]) next.delete('tab')
    else next.set('tab', section)
    return next
  })

  const content = {
    mailboxes: <MailboxesTab domain={domain} />,
    webmail: <WebmailTab username={username} domain={domain} />,
    forwarders: <ForwardersTab username={username} domain={domain} />,
    catchall: <CatchallTab username={username} domain={domain} />,
    spam: <SpamTab username={username} domain={domain} />,
    'spam-entries': <MailboxSpamFiltersTab username={username} domain={domain} />,
    'imap-migrate': <ImapMigrateTab username={username} domain={domain} />,
    routing: <RoutingTab username={username} domain={domain} />,
    delivery: <DeliveryLogTab username={username} />,
  }[activeSection]

  return (
    <div>
      <PageHeader title={config.title} description={config.description} icon={config.sections[0][2] || Mail}>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {domains.length > 0 && <div className="flex items-center gap-2">
            <AtSign className="h-4 w-4 text-muted-foreground" />
            <Select value={domain} onChange={(e) => setDomain(e.target.value)} className="w-56">
              {domains.map((d) => (
                <option key={d.domain} value={d.domain}>{d.domain}</option>
              ))}
            </Select>
          </div>}
        </div>
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
        <div key={`${effectiveMode}:${domain}`}>
          <EmailSectionPicker sections={config.sections} active={activeSection} onChange={selectSection} />
          {content}
        </div>
      )}
    </div>
  )
}
