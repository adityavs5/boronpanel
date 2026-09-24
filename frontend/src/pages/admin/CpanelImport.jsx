import DirectAdminMigration from './DirectAdminMigration'
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, DownloadCloud, FileArchive, Link2, Plus, Upload, Loader2, RefreshCw } from 'lucide-react'
import { get, post } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable, Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

const active = rows => rows.some(job => job.status === 'running' || job.status === 'pending')
const panelLabels = { cpanel: 'cPanel', directadmin: 'DirectAdmin', boron: 'Boron archive' }

function Progress({ row }) {
  return row.status === 'failed' && row.error
    ? <span className="text-sm text-danger">{row.error}</span>
    : <span className="text-sm text-muted-foreground">{row.progress_message || '—'}</span>
}

export default function AccountImports() {
  const qc = useQueryClient()
  const [remoteOpen, setRemoteOpen] = useState(false)
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState(null)
  const [accountPassword, setAccountPassword] = useState(null)
  const [now, setNow] = useState(Date.now())
  const openDetails = row => { setRemoteOpen(false); setAccountPassword(null); setSelected(row) }
  useEffect(() => {
    if (!selected) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [selected?.id])
  const [file, setFile] = useState(null)
  const [form, setForm] = useState({ username: '', panel: 'cpanel', source: 'upload', url: '' })
  const external = useQuery({
    queryKey: ['account-imports'],
    queryFn: () => get('/api/v1/admin/import/accounts'),
    refetchInterval: query => active(query.state.data?.jobs || []) ? 4000 : false,
  })
  const native = useQuery({
    queryKey: ['boron-archive-imports'],
    queryFn: () => get('/api/v1/admin/import/boron'),
    refetchInterval: query => active(query.state.data?.jobs || []) ? 4000 : false,
  })
  const rows = [
    ...(external.data?.jobs || []).map(job => ({ ...job, panel: job.panel || 'cpanel', rowKey: `external-${job.id}` })),
    ...(native.data?.jobs || []).map(job => ({ ...job, panel: 'boron', source: 'upload', rowKey: `boron-${job.id}` })),
  ].sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)))

  const detail = useQuery({
    queryKey: ['account-import-detail', selected?.panel, selected?.id],
    queryFn: () => selected.panel === 'boron'
      ? get(`/api/v1/admin/import/boron/${selected.id}`)
      : get(`/api/v1/admin/import/accounts/${selected.id}?username=${encodeURIComponent(selected.username)}`),
    enabled: !!selected,
    refetchInterval: selected ? 3000 : false,
    refetchIntervalInBackground: true,
    gcTime: 0,
  })
  useEffect(() => {
    if (detail.data?.initial_password) setAccountPassword(detail.data.initial_password)
  }, [detail.data])
  const latestRow = rows.find(row => row.rowKey === selected?.rowKey) || selected
  const shown = detail.data ? { ...latestRow, ...detail.data } : latestRow
  const running = shown && ['pending', 'running'].includes(shown.status)
  const completedItems = shown?.results?.length || 0
  const failedItems = shown?.results?.filter(item => item.status === 'failed').length || 0
  const startTime = shown?.started_at ? Date.parse(/(?:Z|[+-]\d\d:\d\d)$/.test(shown.started_at) ? shown.started_at : `${shown.started_at}Z`) : null
  const elapsed = startTime ? Math.max(0, Math.floor(((shown.completed_at ? Date.parse(/(?:Z|[+-]\d\d:\d\d)$/.test(shown.completed_at) ? shown.completed_at : `${shown.completed_at}Z`) : now) - startTime) / 1000)) : null

  const reset = () => {
    setForm({ username: '', panel: 'cpanel', source: 'upload', url: '' })
    setFile(null)
  }
  const create = useMutation({
    mutationFn: fd => post(form.panel === 'boron' ? '/api/v1/admin/import/boron' : '/api/v1/admin/import/accounts', fd),
    onSuccess: job => {
      toast.success('Import queued', `Account ${job.username} will be restored in the background.`)
      qc.invalidateQueries({ queryKey: ['account-imports'] })
      qc.invalidateQueries({ queryKey: ['boron-archive-imports'] })
      setOpen(false)
      openDetails({ ...job, panel: form.panel, rowKey: `${form.panel === 'boron' ? 'boron' : 'external'}-${job.id}` })
      reset()
    },
    onError: error => toast.error('Could not start import', error.message),
  })
  const submit = event => {
    event.preventDefault()
    const fd = new FormData()
    fd.append('username', form.username.trim())
    if (form.panel !== 'boron') fd.append('panel', form.panel)
    if (form.source === 'url' && form.panel !== 'boron') fd.append('url', form.url.trim())
    else if (file) fd.append('file', file)
    create.mutate(fd)
  }
  const canSubmit = form.username.trim() && (form.source === 'url' && form.panel !== 'boron' ? form.url.trim() : file)
  const copy = async value => {
    try { await navigator.clipboard.writeText(value); toast.success('Password copied') }
    catch { toast.error('Could not copy', 'Select and copy the password manually.') }
  }

  const columns = [
    { key: 'username', header: 'New account', sortable: true, searchable: true, render: row => <span className="font-medium">{row.username}</span> },
    { key: 'panel', header: 'Archive format', sortable: true, render: row => panelLabels[row.panel] || row.panel },
    { key: 'source', header: 'Source', render: row => <span className="inline-flex items-center gap-1.5 text-muted-foreground">{row.source === 'url' ? <Link2 className="h-3.5 w-3.5" /> : <Upload className="h-3.5 w-3.5" />}{row.source === 'directadmin_remote' ? 'DirectAdmin server' : row.source === 'url' ? 'URL' : 'Upload'}</span> },
    { key: 'status', header: 'Status', sortable: true, render: row => <StatusBadge status={row.status} /> },
    { key: 'progress_message', header: 'Progress', render: row => <Progress row={row} /> },
    { key: 'started_at', header: 'Started', sortable: true, render: row => row.started_at ? formatDate(row.started_at) : '—' },
  ]

  return <div>
    <PageHeader title="Account migrations" description="Move complete accounts from cPanel, DirectAdmin, or another Boron server. Each item is verified and reported while the import runs." icon={DownloadCloud}>
      <Button variant="secondary" onClick={() => setRemoteOpen(value => !value)}>From DirectAdmin server</Button>
      <Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" />New migration</Button>
    </PageHeader>
    {remoteOpen && <DirectAdminMigration onClose={() => setRemoteOpen(false)} onQueued={job => openDetails({ ...job, panel: 'directadmin', rowKey: `external-${job.id}` })} />}
    <DataTable columns={columns} data={rows} loading={external.isLoading || native.isLoading} error={external.error || native.error} onRetry={() => { external.refetch(); native.refetch() }} filterable searchPlaceholder="Search migrations…" pageSize={15} initialSort={{ key: 'started_at', dir: 'desc' }} getRowKey={row => row.rowKey} onRowClick={openDetails} emptyTitle="No account migrations" emptyDescription="Upload a full account archive to migrate it to this server." emptyIcon={FileArchive} emptyAction={<Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" />New migration</Button>} />

    <Dialog open={open} onOpenChange={value => { setOpen(value); if (!value) reset() }}>
      <DialogContent size="md"><DialogHeader><DialogTitle>New account migration</DialogTitle><DialogDescription>Choose the panel that created the archive. Boron archives include checksums and restore the original account username.</DialogDescription></DialogHeader>
        <form onSubmit={submit}><DialogBody className="space-y-4">
          <FormField label="Archive format" htmlFor="migration-panel" required><Select id="migration-panel" value={form.panel} onChange={event => setForm(value => ({ ...value, panel: event.target.value, source: 'upload' }))}><option value="cpanel">cPanel / WHM full backup</option><option value="directadmin">DirectAdmin user backup</option><option value="boron">Boron portable account archive</option></Select></FormField>
          <FormField label="New Boron username" htmlFor="migration-username" required hint={form.panel === 'boron' ? 'Must match the username stored in the Boron archive.' : 'Lowercase letters and digits, starting with a letter; maximum 16 characters.'}><Input id="migration-username" autoFocus required pattern="[a-z][a-z0-9]{0,15}" value={form.username} onChange={event => setForm(value => ({ ...value, username: event.target.value }))} placeholder="migrated1" /></FormField>
          {form.panel !== 'boron' && <FormField label="Backup source" htmlFor="migration-source"><Select id="migration-source" value={form.source} onChange={event => setForm(value => ({ ...value, source: event.target.value }))}><option value="upload">Upload from this computer</option><option value="url">Download from a public URL</option></Select></FormField>}
          {form.source === 'url' && form.panel !== 'boron'
            ? <FormField label="Direct archive URL" htmlFor="migration-url" required><Input id="migration-url" type="url" required value={form.url} onChange={event => setForm(value => ({ ...value, url: event.target.value }))} placeholder="https://example.com/account-backup.tar.gz" /></FormField>
            : <FormField label="Account archive" htmlFor="migration-file" required hint="Accepted formats: .tar, .tar.gz, .tgz, and .boron.tar"><Input id="migration-file" type="file" required accept=".tar,.tar.gz,.tgz,.boron.tar" className="h-auto py-1.5 file:mr-3 file:rounded-btn file:border-0 file:bg-muted file:px-3 file:py-1 file:text-sm" onChange={event => setFile(event.target.files?.[0] || null)} /></FormField>}
        </DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" loading={create.isPending} disabled={!canSubmit}><DownloadCloud className="h-4 w-4" />Start migration</Button></DialogFooter></form>
      </DialogContent>
    </Dialog>

    <Dialog open={!!selected} onOpenChange={value => { if (!value) { setSelected(null); setAccountPassword(null) } }}>
      <DialogContent size="lg"><DialogHeader><DialogTitle>Migration #{shown?.id} · {shown?.username}</DialogTitle><DialogDescription>{panelLabels[shown?.panel]}{shown?.started_at ? ` · started ${formatDate(shown.started_at)}` : ''}</DialogDescription></DialogHeader>
        <DialogBody className="space-y-5">
          {shown && <>
            <section className="rounded-btn border border-border bg-muted/30 p-4 space-y-3" aria-label="Migration progress">
              <div className="flex flex-wrap items-center gap-3"><StatusBadge status={shown.status} />{running && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}<p role="status" aria-live="polite" className="font-semibold">{shown.progress_message || (running ? 'Waiting for the worker…' : shown.status)}</p></div>
              <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted-foreground"><span>{completedItems} items processed</span><span>{failedItems} failed</span>{elapsed !== null && <span>Elapsed: {Math.floor(elapsed / 60)}m {elapsed % 60}s</span>}</div>
              {running && <p className="text-sm text-muted-foreground">Updates automatically every 3 seconds. Source backup creation can take several minutes; per-item results appear once restoration begins. You can close this window without stopping the migration.</p>}
              <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground"><span>{detail.isFetching ? 'Checking latest progress…' : detail.dataUpdatedAt ? `Last checked ${new Date(detail.dataUpdatedAt).toLocaleTimeString()}` : 'Loading progress…'}</span><Button variant="secondary" size="sm" onClick={() => detail.refetch()} disabled={detail.isFetching}><RefreshCw className="h-3.5 w-3.5" />Refresh progress</Button></div>
            </section>
            {detail.error && <div role="alert" className="text-danger text-sm">Could not refresh progress: {detail.error.message}. Retrying automatically; last known status is shown.</div>}
            {accountPassword && <div className="rounded-panel border border-warning/40 bg-warning/10 p-4"><p className="font-semibold">New account password</p><p className="mt-1 text-sm text-muted-foreground">Save this now. It is displayed only once.</p><div className="mt-3 flex gap-2"><code className="min-w-0 flex-1 select-all break-all rounded-btn border border-border bg-card p-2 font-mono" data-bwignore="true">{accountPassword}</code><Button variant="secondary" onClick={() => copy(accountPassword)}><Copy className="h-4 w-4" />Copy</Button></div></div>}
            {shown.panel === 'boron' && <div className="grid gap-3 sm:grid-cols-2"><div className="rounded-btn border border-border p-3"><p className="text-xs text-muted-foreground">Archive version</p><p className="font-semibold">{shown.archive_version || '—'}</p></div><div className="rounded-btn border border-border p-3"><p className="text-xs text-muted-foreground">Verified components</p><p className="font-semibold">{shown.components_verified ?? '—'}</p></div></div>}
            {shown.error && <div className="rounded-btn border border-danger/30 bg-danger/10 p-3 text-sm text-danger">{shown.error}</div>}
            {shown.panel !== 'boron' && <div><h3 className="mb-2 text-sm font-semibold">Per-item report</h3>{shown.results?.length ? <div className="max-h-80 overflow-auto rounded-btn border border-border"><Table><THead><TR><TH>Item</TH><TH>Status</TH><TH>Detail</TH></TR></THead><TBody>{shown.results.map((result, index) => <TR key={index}><TD className="whitespace-nowrap font-mono text-xs">{result.item}</TD><TD><StatusBadge status={result.status} /></TD><TD className="text-sm text-muted-foreground">{result.detail || '—'}</TD></TR>)}</TBody></Table></div> : <p className="text-sm text-muted-foreground">{running ? 'Preparing the source archive. No restoration items have completed yet.' : 'No items were processed.'}</p>}</div>}
          </>}
        </DialogBody><DialogFooter><Button variant="secondary" onClick={() => { setSelected(null); setAccountPassword(null) }}>Close</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
}
