import '@/customer-polish.css'
import { SnapshotRunDialog } from '@/components/backups/SnapshotHistory'
import { SnapshotRestoreHistory } from '@/components/backups/SnapshotRestore'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Archive, Plus, RotateCcw, RefreshCw, FolderOpen, Download, Mail, MoreHorizontal } from 'lucide-react'
import { get, patch, post } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatBytes, formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Badge } from '@/components/ui/Badge'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'
import { useAuth } from '@/store/auth'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/DropdownMenu'

const BACKUP_KINDS = [
  { value: 'full', label: 'Full account (files + databases + mail + DNS + config)' },
  { value: 'file', label: 'Single file / directory' },
  { value: 'database', label: 'Single database' },
  { value: 'databases', label: 'All databases' },
  { value: 'mailbox', label: 'Single mailbox' },
]
const BACKUP_EMAIL_EVENTS=[['backup.completed','Backup completed'],['backup.failed','Backup failed'],['backup.partial','Completed with warnings'],['backup.overdue','Backup overdue'],['backup.destination_unavailable','Destination unavailable'],['backup.restore_completed','Restore completed'],['backup.restore_failed','Restore failed'],['backup.download_ready','Download ready']]

function BackupEmailPreferences({ username }) {
  const endpoint=`/api/v1/accounts/${encodeURIComponent(username)}/notification-prefs`
  const preferences=useQuery({queryKey:['backup-email-preferences',username],queryFn:()=>get(endpoint),enabled:!!username})
  const [form,setForm]=useState(null)
  useEffect(()=>{if(preferences.data)setForm({customer_email:preferences.data.customer_email||'',events:{...preferences.data.events}})},[preferences.data])
  const save=useMutation({mutationFn:()=>patch(endpoint,form),onSuccess:data=>{setForm({customer_email:data.customer_email||'',events:{...data.events}});toast.success('Backup email preferences saved')}})
  if(!form)return preferences.error ? <ErrorState error={preferences.error} onRetry={preferences.refetch} /> : <CenteredSpinner />
  return <section className="rounded-card border border-border bg-card p-5"><div className="mb-4"><h2 className="text-lg font-semibold">Backup email notifications</h2><p className="text-sm text-muted-foreground">Choose which backup activity is sent to your account email. Server notification settings still apply.</p></div><div className="grid gap-5 lg:grid-cols-[minmax(0,22rem)_1fr]"><FormField label="Notification email"><Input type="email" value={form.customer_email} onChange={event=>setForm(value=>({...value,customer_email:event.target.value}))} placeholder="you@example.com"/></FormField><fieldset><legend className="mb-2 text-sm font-medium">Events</legend><div className="grid gap-2 sm:grid-cols-2">{BACKUP_EMAIL_EVENTS.map(([key,label])=><label key={key} className="flex items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4 accent-accent" checked={form.events[key]??true} onChange={event=>setForm(value=>({...value,events:{...value.events,[key]:event.target.checked}}))}/>{label}</label>)}</div></fieldset></div>{save.error&&<p className="mt-3 text-sm text-danger">{save.error.message}</p>}<Button className="mt-4" loading={save.isPending} onClick={()=>save.mutate()}>Save email preferences</Button></section>
}

// Poll while any job is still working so progress + status stay live.
const isActive = (rows) => rows.some((j) => j.status === 'running' || j.status === 'pending')

function ProgressCell({ row }) {
  if (row.status === 'failed' && row.error) {
    return <span className="text-sm text-danger">{row.error}</span>
  }
  return <span className="text-sm text-muted-foreground">{row.progress_message || '—'}</span>
}

function KindCell({ row }) {
  return (
    <span className="font-medium capitalize text-foreground">
      {row.kind}
      {row.item_ref ? <span className="ml-1 font-normal text-muted-foreground">({row.item_ref})</span> : null}
    </span>
  )
}

function Stat({ label, value }) {
  return (
    <div className="rounded-btn border border-border bg-muted/40 px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="truncate text-sm font-medium text-foreground" title={String(value)}>{value}</div>
    </div>
  )
}

function BadgeList({ label, items }) {
  if (!items?.length) return null
  return (
    <div>
      <div className="mb-2 text-sm font-medium text-foreground">
        {label} <span className="font-normal text-muted-foreground">({items.length})</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((it, i) => (
          <Badge key={`${it}-${i}`} variant="outline">{it}</Badge>
        ))}
      </div>
    </div>
  )
}

// Read-only browser for a completed backup point. GETs the browse endpoint,
// which returns {kind:'full', manifest, contents:[...]} for full backups or
// {kind, item_ref} for single-item (file/database/mailbox) backups.
function BrowseDialog({ username, job, onOpenChange }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['backup-browse', username, job?.id],
    queryFn: () => get(`/api/v1/accounts/${username}/backups/${job.id}/browse`),
    enabled: !!username && !!job,
  })

  const manifest = data?.manifest
  const contents = data?.contents || []

  return (
    <Dialog open={!!job} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Browse backup #{job?.id}</DialogTitle>
          <DialogDescription>
            {job ? `${job.kind}${job.item_ref ? ` (${job.item_ref})` : ''} backup` : 'Backup'} contents.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-5">
          {isLoading ? (
            <CenteredSpinner />
          ) : error ? (
            <ErrorState error={error} onRetry={refetch} title="Could not read backup" />
          ) : ['full', 'databases'].includes(data?.kind) && manifest ? (
            <>
              {data.kind === 'full' && <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Backed up" value={formatDate(manifest.backed_up_at)} />
                <Stat label="PHP version" value={manifest.php_version || '—'} />
                <Stat label="Domains" value={manifest.domains?.length ?? 0} />
                <Stat label="Databases" value={manifest.databases?.length ?? 0} />
                <Stat label="Mail domains" value={manifest.mail_domains?.length ?? 0} />
                <Stat label="Mailboxes" value={manifest.mail_users?.length ?? 0} />
                <Stat label="Cron jobs" value={manifest.cron_jobs?.length ?? 0} />
                <Stat label="DNS zone" value={manifest.dns_zone?.zone || 'none'} />
              </div>}

              {data.kind === 'full' && <><BadgeList label="Domains" items={(manifest.domains || []).map((d) => d.domain)} /><BadgeList label="Databases" items={(manifest.databases || []).map((d) => d.db_name)} /><BadgeList label="Mailboxes" items={(manifest.mail_users || []).map((u) => `${u.local_part}@${u.domain}`)} /></>}
              {data.kind === 'databases' && <BadgeList label="Databases" items={manifest.databases || []} />}

              <div>
                <div className="mb-2 text-sm font-medium text-foreground">
                  Archive members <span className="font-normal text-muted-foreground">({contents.length})</span>
                </div>
                {contents.length === 0 ? (
                  <div className="rounded-btn border border-border bg-muted/40 px-4 py-8 text-center text-sm text-muted-foreground">
                    This archive has no listable members.
                  </div>
                ) : (
                  <pre className="max-h-[40vh] overflow-auto rounded-btn border border-border bg-muted/40 p-4 font-mono text-xs leading-relaxed text-foreground whitespace-pre-wrap">
                    {contents.join('\n')}
                  </pre>
                )}
              </div>
            </>
          ) : (
            <div className="rounded-btn border border-border bg-muted/40 px-4 py-6 text-sm text-muted-foreground">
              This is a <span className="font-medium capitalize text-foreground">{data?.kind}</span> backup of{' '}
              <code className="font-mono text-foreground">{data?.item_ref || job?.item_ref}</code>. It contains a single
              item, so there is no archive listing to browse — restore it from the backups list.
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default function Backups() {
  const username = useAccountUsername()
  const isAdmin = useAuth(state => state.role === 'admin')
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [section, setSection] = useState(searchParams.get('view') === 'restores' ? 'history' : 'inventory')
  const [backupType, setBackupType] = useState('all')
  const [snapshot, setSnapshot] = useState(null)

  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ kind: 'full', item_ref: '', mail_domain: '' })
  const [toRestore, setToRestore] = useState(null)
  const [toBrowse, setToBrowse] = useState(null)

  useEffect(() => {
    const kind = searchParams.get('kind')
    if (searchParams.get('action') === 'create' && BACKUP_KINDS.some(option => option.value === kind)) {
      setForm({ kind, item_ref: '', mail_domain: '' })
      setCreateOpen(true)
      setSearchParams({}, { replace: true })
    } else if (searchParams.get('view') === 'restores') {
      setSection('history')
      setSearchParams({}, { replace: true })
    }
  }, [searchParams, setSearchParams])

  const backups = useQuery({
    queryKey: ['backups', username],
    queryFn: () => get(`/api/v1/accounts/${username}/backups`),
    enabled: !!username,
    refetchInterval: (query) => (isActive(query.state.data?.jobs || []) ? 5000 : false),
  })

  const snapshots = useQuery({
    queryKey: ['snapshot-runs', username, username],
    queryFn: () => get(`/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots/runs`),
    enabled: !!username,
    refetchInterval: query => isActive(query.state.data?.runs || []) ? 3000 : false,
  })

  const restores = useQuery({
    queryKey: ['backup-restores', username],
    queryFn: () => get(`/api/v1/accounts/${username}/backups/restores/list`),
    enabled: !!username,
    refetchInterval: (query) => (isActive(query.state.data?.restore_jobs || []) ? 5000 : false),
  })

  const databases = useQuery({
    queryKey: ['databases', username],
    queryFn: () => get(`/api/v1/accounts/${username}/databases`),
    enabled: !!username && createOpen,
  })
  const mailboxInventory = useQuery({
    queryKey: ['backup-mailbox-inventory', username],
    queryFn: () => get(`/api/v1/accounts/${username}/email/mailboxes`),
    enabled: !!username && createOpen && form.kind === 'mailbox',
  })

  const createMut = useMutation({
    mutationFn: (body) => post(`/api/v1/accounts/${username}/backups`, body),
    onSuccess: () => {
      toast.success('Backup started', 'The backup job is running in the background.')
      qc.invalidateQueries({ queryKey: ['backups', username] })
      setSection('inventory')
      setBackupType('all')
      setCreateOpen(false)
      setForm({ kind: 'full', item_ref: '', mail_domain: '' })
    },
  })

  const restoreMut = useMutation({
    mutationFn: (job) => post(`/api/v1/accounts/${username}/backups/${job.id}/restore`, {}),
    onSuccess: () => {
      toast.success('Restore started', 'The restore job is running in the background.')
      qc.invalidateQueries({ queryKey: ['backups', username] })
      qc.invalidateQueries({ queryKey: ['backup-restores', username] })
      setSection('history')
      setToRestore(null)
    },
  })

  const needsItemRef = !['full', 'databases'].includes(form.kind)

  const inventory = [
    ...(snapshots.data?.runs || []).map(row => ({ ...row, _source: 'snapshot', _key: `snapshot-${row.id}`, kind: (row.options?.components || []).map(value => ({files: 'Files', databases: 'Databases', mail: 'Email', config: 'Configuration'})[value] || value).join(', ') || 'Recovery point' })),
    ...(backups.data?.jobs || []).map(row => ({ ...row, _source: 'archive', _key: `archive-${row.id}` })),
  ].filter(row => backupType === 'all' || row._source === backupType)

  const backupColumns = [
    { key: 'id', header: 'Backup', render: row => row._source === 'snapshot' ? <button className="font-medium text-accent hover:underline" onClick={() => setSnapshot(row)}>Backup #{row.id}</button> : `Archive #${row.id}` },
    { key: '_source', header: 'Backup type', sortable: true, render: row => row._source === 'snapshot' ? 'Scheduled recovery point' : 'On-demand archive' },
    { key: 'kind', header: 'Kind', sortable: true, searchable: true, render: (r) => <KindCell row={r} /> },
    { key: 'status', header: 'Status', sortable: true, render: (r) => <StatusBadge status={r.status} /> },
    { key: 'trigger', header: 'Trigger', sortable: true, render: (r) => <span className="capitalize text-muted-foreground">{r.trigger || (r._source === 'snapshot' ? 'Provider backup job' : '—')}</span> },
    {
      key: 'size_bytes',
      header: 'Size',
      sortable: true,
      align: 'right',
      sortValue: (r) => (r._source === 'snapshot' ? r.summary?.total_bytes_processed : r.size_bytes) ?? 0,
      render: (r) => <span className="tabular-nums">{(r._source === 'snapshot' ? r.summary?.total_bytes_processed : r.size_bytes) != null ? formatBytes(r._source === 'snapshot' ? r.summary.total_bytes_processed : r.size_bytes) : '—'}</span>,
    },
    { key: 'started_at', header: 'Started', sortable: true, render: (r) => formatDate(r.started_at) },
    { key: 'progress_message', header: 'Progress', render: (r) => <ProgressCell row={r} /> },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (r) =>
        r._source === 'snapshot' ? <Button size="sm" variant="secondary" onClick={() => setSnapshot(r)}>View details</Button> : r.status === 'completed' ? (
          <div className="flex justify-end gap-1">
            <Button variant="outline" size="sm" onClick={() => setToRestore(r)}>
              <RotateCcw className="h-4 w-4" /> Restore
            </Button>
            <DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="icon-sm" aria-label={`Actions for backup ${r.id}`}><MoreHorizontal className="h-4 w-4"/></Button></DropdownMenuTrigger><DropdownMenuContent align="end"><DropdownMenuItem onSelect={() => setToBrowse(r)}><FolderOpen className="h-4 w-4"/>Browse</DropdownMenuItem>{isAdmin && r.kind === 'full' && <DropdownMenuItem onSelect={() => window.open(`/api/v1/admin/account-archives/${r.id}/download`, '_blank', 'noopener')}><Download className="h-4 w-4"/>Portable archive</DropdownMenuItem>}</DropdownMenuContent></DropdownMenu>
          </div>
        ) : null,
    },
  ]


  return (
    <div>
      <PageHeader title="Backups" description="Create backup points of your account and restore them when you need to." icon={Archive} primaryAction={<Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Create backup
        </Button>} />

      <Tabs value={section} onValueChange={setSection}>
        <TabsList aria-label="Backup sections">
          <TabsTrigger value="inventory">Backups</TabsTrigger>
          <TabsTrigger value="history">Restore history</TabsTrigger>
          <TabsTrigger value="notifications">Email notifications</TabsTrigger>
        </TabsList>
        <TabsContent value="inventory">
          <div className="mb-4 flex flex-wrap items-end gap-3"><FormField className="w-full max-w-sm" label="Backup type" htmlFor="customer-backup-type"><Select id="customer-backup-type" value={backupType} onChange={event => setBackupType(event.target.value)}>
            <option value="all">All backup types</option><option value="snapshot">Scheduled recovery points</option><option value="archive">On-demand archives</option>
          </Select></FormField><Button variant="ghost" loading={snapshots.isFetching || backups.isFetching} onClick={() => { snapshots.refetch(); backups.refetch() }}><RefreshCw className="h-4 w-4"/>Refresh</Button></div>
          <p className="customer-backup-description">Scheduled recovery points are created by your hosting provider. On-demand archives are backups you create here. View a recovery point’s details to browse, download or restore its contents.</p>
          <DataTable
            columns={backupColumns}
            data={inventory}
            loading={backups.isLoading || snapshots.isLoading}
            error={backups.error || snapshots.error}
            onRetry={() => { backups.refetch(); snapshots.refetch() }}
            filterable
            searchPlaceholder="Search backups…"
            pageSize={15}
            initialSort={{ key: 'started_at', dir: 'desc' }}
            getRowKey={row => row._key}
            emptyTitle={backupType === 'snapshot' ? 'No scheduled recovery points yet' : backupType === 'archive' ? 'No on-demand archives yet' : 'No backups yet'}
            emptyDescription={backupType === 'snapshot' ? 'Recovery points from your hosting provider’s backup jobs will appear here.' : 'Create a full or partial archive backup to protect your account.'}
            emptyIcon={Archive}
            emptyAction={backupType !== 'snapshot' ? <Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Create backup</Button> : undefined}
          />
          <p className="customer-backup-description mt-4">Archives use your configured backup destination. If no archive schedule exists, Boron uses protected local storage and keeps the latest three archives of each type.</p>
        </TabsContent>
        <TabsContent value="history"><SnapshotRestoreHistory username={username} archiveHistory={restores} showHeading={false}/></TabsContent>
        <TabsContent value="notifications" forceMount hidden={section !== 'notifications'}><BackupEmailPreferences username={username} /></TabsContent>
      </Tabs>
      {snapshot && <SnapshotRunDialog run={snapshots.data?.runs?.find(row => row.id === snapshot.id) || snapshot} username={username} showRestoreHistory={false} onClose={() => setSnapshot(null)} />}

      {/* Create backup */}
      <Dialog open={createOpen} onOpenChange={open => { if (!createMut.isPending) setCreateOpen(open) }}>
        <DialogContent
          size="sm"
          showClose={!createMut.isPending}
          onEscapeKeyDown={event => { if (createMut.isPending) event.preventDefault() }}
          onPointerDownOutside={event => { if (createMut.isPending) event.preventDefault() }}
        >
          <DialogHeader>
            <DialogTitle>Create backup</DialogTitle>
            <DialogDescription>A backup runs in the background and appears in the list when complete.</DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({
                kind: form.kind,
                item_ref: needsItemRef ? form.item_ref.trim() || undefined : undefined,
              })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="What to back up" required>
                <Select value={form.kind} onChange={(e) => setForm({ kind: e.target.value, item_ref: '', mail_domain: '' })}>
                  {BACKUP_KINDS.map((k) => (
                    <option key={k.value} value={k.value}>{k.label}</option>
                  ))}
                </Select>
              </FormField>
              {form.kind === 'database' && (
                <FormField label="Database" required hint="Only databases owned by this account are available.">
                  <Select autoFocus value={form.item_ref} onChange={(e) => setForm((f) => ({ ...f, item_ref: e.target.value }))} required>
                    <option value="">Choose a database…</option>
                    {(databases.data?.databases || []).map((database) => <option key={database.db_name} value={database.db_name}>{database.db_name}</option>)}
                  </Select>
                </FormField>
              )}
              {form.kind === 'mailbox' && (
                <div className="space-y-4">
                  <FormField label="Mail domain" required hint="Choose the domain first so the mailbox list stays clear.">
                    <Select
                      autoFocus
                      value={form.mail_domain}
                      onChange={(e) => setForm((f) => ({ ...f, mail_domain: e.target.value, item_ref: '' }))}
                      disabled={mailboxInventory.isLoading || mailboxInventory.isError}
                      required
                    >
                      <option value="">{mailboxInventory.isLoading ? 'Loading mail domains…' : 'Choose a mail domain…'}</option>
                      {(mailboxInventory.data?.domains || []).map((name) => <option key={name} value={name}>{name}</option>)}
                    </Select>
                  </FormField>
                  <FormField label="Mailbox" required hint="The full email address is stored with the backup.">
                    <Select
                      value={form.item_ref}
                      onChange={(e) => setForm((f) => ({ ...f, item_ref: e.target.value }))}
                      disabled={!form.mail_domain || mailboxInventory.isLoading || mailboxInventory.isError}
                      required
                    >
                      <option value="">Choose a mailbox…</option>
                      {(mailboxInventory.data?.mailboxes || []).filter((item) => item.domain === form.mail_domain).map((item) => (
                        <option key={item.address} value={item.address}>{item.address}{item.active === false ? ' (inactive)' : ''}</option>
                      ))}
                    </Select>
                  </FormField>
                  {mailboxInventory.isError && <p className="text-sm text-danger">Could not load mailboxes. {mailboxInventory.error.message}</p>}
                  {!mailboxInventory.isLoading && !mailboxInventory.isError && !(mailboxInventory.data?.mailboxes || []).length && (
                    <div className="rounded-btn border border-border bg-muted/40 p-4 text-sm">
                      <div className="flex items-center gap-2 font-medium"><Mail className="h-4 w-4" /> No email accounts yet</div>
                      <p className="mt-1 text-muted-foreground">Create an email account before making a mailbox backup.</p>
                    </div>
                  )}
                </div>
              )}
              {needsItemRef && !['database', 'mailbox'].includes(form.kind) && (
                <FormField
                  label="Item reference"
                  required
                  hint="Enter a path inside your account, such as public_html/wp-config.php."
                >
                  <Input
                    autoFocus
                    value={form.item_ref}
                    onChange={(e) => setForm((f) => ({ ...f, item_ref: e.target.value }))}
                    placeholder="public_html/wp-config.php"
                    required
                  />
                </FormField>
              )}
            </DialogBody>
            <DialogFooter>
              <div className="mr-auto min-w-0 text-sm">
                {createMut.isPending && <p role="status" className="text-muted-foreground">Starting the backup… this can take up to 30 seconds. Do not close this page.</p>}
                {!createMut.isPending && createMut.error && <p role="alert" className="text-danger">{createMut.error.message}</p>}
              </div>
              <Button type="button" variant="secondary" disabled={createMut.isPending} onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button
                type="submit"
                loading={createMut.isPending}
                disabled={(needsItemRef && !form.item_ref.trim()) || (form.kind === 'mailbox' && !form.mail_domain) || (form.kind === 'databases' && !(databases.data?.databases || []).length)}
              >
                Back up now
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Browse backup contents */}
      <BrowseDialog
        username={username}
        job={toBrowse}
        onOpenChange={(v) => { if (!v) setToBrowse(null) }}
      />

      {/* Restore */}
      <ConfirmDialog
        open={!!toRestore}
        onOpenChange={(v) => { if (!v) setToRestore(null) }}
        title={toRestore ? `Restore ${toRestore.kind} backup?` : 'Restore backup?'}
        description="This overwrites the current data with the contents of this backup point. This cannot be undone."
        confirmLabel="Restore"
        loading={restoreMut.isPending}
        loadingDescription="Starting the restore… this can take up to 30 seconds. Do not close this page."
        error={restoreMut.error}
        onConfirm={() => toRestore && restoreMut.mutate(toRestore)}
      />
    </div>
  )
}
