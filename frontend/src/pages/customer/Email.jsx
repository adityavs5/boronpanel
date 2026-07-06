import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Mail, Plus, Trash2, Inbox, Forward, ShieldAlert, AtSign } from 'lucide-react'
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

// --- Page ----------------------------------------------------------------

export default function Email() {
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
        <Tabs defaultValue="mailboxes" key={domain}>
          <TabsList>
            <TabsTrigger value="mailboxes"><Inbox className="h-4 w-4" /> Mailboxes</TabsTrigger>
            <TabsTrigger value="forwarders"><Forward className="h-4 w-4" /> Forwarders</TabsTrigger>
            <TabsTrigger value="catchall"><AtSign className="h-4 w-4" /> Catch-all</TabsTrigger>
            <TabsTrigger value="spam"><ShieldAlert className="h-4 w-4" /> Spam filter</TabsTrigger>
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
        </Tabs>
      )}
    </div>
  )
}
