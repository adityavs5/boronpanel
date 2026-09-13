import { SnapshotHistory } from '@/components/backups/SnapshotHistory'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Archive, Plus, RotateCcw, History, FolderOpen } from 'lucide-react'
import { get, post } from '@/lib/api'
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

const BACKUP_KINDS = [
  { value: 'full', label: 'Full account (files + databases + mail + DNS + config)' },
  { value: 'file', label: 'Single file / directory' },
  { value: 'database', label: 'Single database' },
  { value: 'mailbox', label: 'Single mailbox' },
]

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
          ) : data?.kind === 'full' && manifest ? (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Backed up" value={formatDate(manifest.backed_up_at)} />
                <Stat label="PHP version" value={manifest.php_version || '—'} />
                <Stat label="Domains" value={manifest.domains?.length ?? 0} />
                <Stat label="Databases" value={manifest.databases?.length ?? 0} />
                <Stat label="Mail domains" value={manifest.mail_domains?.length ?? 0} />
                <Stat label="Mailboxes" value={manifest.mail_users?.length ?? 0} />
                <Stat label="Cron jobs" value={manifest.cron_jobs?.length ?? 0} />
                <Stat label="DNS zone" value={manifest.dns_zone?.zone || 'none'} />
              </div>

              <BadgeList label="Domains" items={(manifest.domains || []).map((d) => d.domain)} />
              <BadgeList label="Databases" items={(manifest.databases || []).map((d) => d.db_name)} />
              <BadgeList label="Mailboxes" items={(manifest.mail_users || []).map((u) => `${u.local_part}@${u.domain}`)} />

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
  const qc = useQueryClient()

  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ kind: 'full', item_ref: '' })
  const [toRestore, setToRestore] = useState(null)
  const [toBrowse, setToBrowse] = useState(null)

  const backups = useQuery({
    queryKey: ['backups', username],
    queryFn: () => get(`/api/v1/accounts/${username}/backups`),
    enabled: !!username,
    refetchInterval: (query) => (isActive(query.state.data?.jobs || []) ? 5000 : false),
  })

  const restores = useQuery({
    queryKey: ['backup-restores', username],
    queryFn: () => get(`/api/v1/accounts/${username}/backups/restores/list`),
    enabled: !!username,
    refetchInterval: (query) => (isActive(query.state.data?.restore_jobs || []) ? 5000 : false),
  })

  const createMut = useMutation({
    mutationFn: (body) => post(`/api/v1/accounts/${username}/backups`, body),
    onSuccess: () => {
      toast.success('Backup started', 'The backup job is running in the background.')
      qc.invalidateQueries({ queryKey: ['backups', username] })
      setCreateOpen(false)
      setForm({ kind: 'full', item_ref: '' })
    },
    onError: (e) => toast.error('Could not start backup', e.message),
  })

  const restoreMut = useMutation({
    mutationFn: (job) => post(`/api/v1/accounts/${username}/backups/${job.id}/restore`, {}),
    onSuccess: () => {
      toast.success('Restore started', 'The restore job is running in the background.')
      qc.invalidateQueries({ queryKey: ['backups', username] })
      qc.invalidateQueries({ queryKey: ['backup-restores', username] })
      setToRestore(null)
    },
    onError: (e) => toast.error('Could not start restore', e.message),
  })

  const needsItemRef = form.kind !== 'full'

  const backupColumns = [
    { key: 'kind', header: 'Kind', sortable: true, searchable: true, render: (r) => <KindCell row={r} /> },
    { key: 'status', header: 'Status', sortable: true, render: (r) => <StatusBadge status={r.status} /> },
    { key: 'trigger', header: 'Trigger', sortable: true, render: (r) => <span className="capitalize text-muted-foreground">{r.trigger || '—'}</span> },
    {
      key: 'size_bytes',
      header: 'Size',
      sortable: true,
      align: 'right',
      sortValue: (r) => r.size_bytes ?? 0,
      render: (r) => <span className="tabular-nums">{r.size_bytes ? formatBytes(r.size_bytes) : '—'}</span>,
    },
    { key: 'started_at', header: 'Started', sortable: true, render: (r) => formatDate(r.started_at) },
    { key: 'progress_message', header: 'Progress', render: (r) => <ProgressCell row={r} /> },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (r) =>
        r.status === 'completed' ? (
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setToBrowse(r)}>
              <FolderOpen className="h-4 w-4" /> Browse
            </Button>
            <Button variant="outline" size="sm" onClick={() => setToRestore(r)}>
              <RotateCcw className="h-4 w-4" /> Restore
            </Button>
          </div>
        ) : null,
    },
  ]

  const restoreColumns = [
    { key: 'kind', header: 'Kind', sortable: true, searchable: true, render: (r) => <KindCell row={r} /> },
    { key: 'status', header: 'Status', sortable: true, render: (r) => <StatusBadge status={r.status} /> },
    { key: 'started_at', header: 'Started', sortable: true, render: (r) => formatDate(r.started_at) },
    { key: 'completed_at', header: 'Completed', sortable: true, render: (r) => formatDate(r.completed_at) },
    { key: 'progress_message', header: 'Progress', render: (r) => <ProgressCell row={r} /> },
  ]

  return (
    <div>
      <PageHeader title="Backups" description="Create backup points of your account and restore them when you need to." icon={Archive}>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Create backup
        </Button>
      </PageHeader>

      <SnapshotHistory username={username} />
      <h2 className="mb-3 text-lg font-semibold">On-demand archive backups</h2>
      <DataTable
        columns={backupColumns}
        data={backups.data?.jobs}
        loading={backups.isLoading}
        error={backups.error}
        onRetry={backups.refetch}
        filterable
        searchPlaceholder="Search backups…"
        pageSize={15}
        initialSort={{ key: 'started_at', dir: 'desc' }}
        getRowKey={(r) => r.id}
        emptyTitle="No backups yet"
        emptyDescription="Create a full or partial backup point to protect your account."
        emptyIcon={Archive}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Create backup</Button>}
      />

      <div className="mt-8">
        <div className="mb-3 flex items-center gap-2">
          <History className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-base font-semibold text-foreground">Restore history</h2>
        </div>
        <DataTable
          columns={restoreColumns}
          data={restores.data?.restore_jobs}
          loading={restores.isLoading}
          error={restores.error}
          onRetry={restores.refetch}
          pageSize={10}
          initialSort={{ key: 'started_at', dir: 'desc' }}
          getRowKey={(r) => r.id}
          emptyTitle="No restores yet"
          emptyDescription="Restoring a completed backup will show its progress here."
          emptyIcon={History}
        />
      </div>

      {/* Create backup */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent size="sm">
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
                <Select value={form.kind} onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value }))}>
                  {BACKUP_KINDS.map((k) => (
                    <option key={k.value} value={k.value}>{k.label}</option>
                  ))}
                </Select>
              </FormField>
              {needsItemRef && (
                <FormField
                  label="Item reference"
                  required
                  hint="e.g. public_html/wp-config.php, a database name, or user@domain."
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
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button
                type="submit"
                loading={createMut.isPending}
                disabled={needsItemRef && !form.item_ref.trim()}
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
        onConfirm={() => toRestore && restoreMut.mutate(toRestore)}
      />
    </div>
  )
}
