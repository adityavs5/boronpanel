import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { DownloadCloud, Plus, Upload, Link2, FileArchive } from 'lucide-react'
import { get, post } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable, Table, THead, TBody, TR, TH, TD } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

// Poll while any job is still working so progress + status stay live.
const isActive = (rows) => rows.some((j) => j.status === 'running' || j.status === 'pending')

const SOURCE_LABELS = { upload: 'Upload', url: 'URL' }

function ProgressCell({ row }) {
  if (row.status === 'failed' && row.error) {
    return <span className="text-sm text-danger">{row.error}</span>
  }
  return <span className="text-sm text-muted-foreground">{row.progress_message || '—'}</span>
}

export default function CpanelImport() {
  // Works for admin too; this admin-only tool keys its job list off it.
  const username = useAccountUsername()
  const qc = useQueryClient()

  const [createOpen, setCreateOpen] = useState(false)
  const [source, setSource] = useState('upload')
  const [form, setForm] = useState({ username: '', url: '' })
  const [file, setFile] = useState(null)
  const [selectedId, setSelectedId] = useState(null)

  const jobs = useQuery({
    queryKey: ['cpanel-import-jobs', username],
    queryFn: () => get('/api/v1/admin/import/cpanel'),
    refetchInterval: (query) => (isActive(query.state.data?.jobs || []) ? 4000 : false),
  })

  const rows = jobs.data?.jobs || []
  const selected = rows.find((j) => j.id === selectedId) || null

  const resetForm = () => {
    setForm({ username: '', url: '' })
    setFile(null)
    setSource('upload')
  }

  const createMut = useMutation({
    // Multipart: pass a FormData body and let axios set the multipart
    // Content-Type (with boundary) for us.
    mutationFn: (fd) => post('/api/v1/admin/import/cpanel', fd),
    onSuccess: (job) => {
      toast.success('Import started', `Importing into a new account "${job.username}". This runs in the background.`)
      qc.invalidateQueries({ queryKey: ['cpanel-import-jobs', username] })
      setCreateOpen(false)
      resetForm()
    },
    onError: (e) => toast.error('Could not start import', e.message),
  })

  const canSubmit = form.username.trim() && (source === 'url' ? form.url.trim() : !!file)

  const submit = (e) => {
    e.preventDefault()
    if (!canSubmit) return
    const fd = new FormData()
    fd.append('username', form.username.trim())
    if (source === 'url') fd.append('url', form.url.trim())
    else if (file) fd.append('file', file)
    createMut.mutate(fd)
  }

  const columns = [
    {
      key: 'username',
      header: 'New account',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.username}</span>,
    },
    {
      key: 'source',
      header: 'Source',
      sortable: true,
      render: (r) => (
        <span className="inline-flex items-center gap-1.5 text-muted-foreground">
          {r.source === 'url' ? <Link2 className="h-3.5 w-3.5" /> : <Upload className="h-3.5 w-3.5" />}
          {SOURCE_LABELS[r.source] || r.source}
        </span>
      ),
    },
    { key: 'status', header: 'Status', sortable: true, render: (r) => <StatusBadge status={r.status} /> },
    { key: 'progress_message', header: 'Progress', render: (r) => <ProgressCell row={r} /> },
    { key: 'started_at', header: 'Started', sortable: true, render: (r) => (r.started_at ? formatDate(r.started_at) : '—') },
  ]

  return (
    <div>
      <PageHeader
        title="cPanel import"
        description="Import a standard cPanel/WHM full-account backup tarball into a brand-new Boron account. Unsupported items are skipped and reported individually rather than aborting the whole import."
        icon={DownloadCloud}
      >
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> New import
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={rows}
        loading={jobs.isLoading}
        error={jobs.error}
        onRetry={jobs.refetch}
        filterable
        searchPlaceholder="Search import jobs…"
        pageSize={15}
        initialSort={{ key: 'started_at', dir: 'desc' }}
        getRowKey={(r) => r.id}
        onRowClick={(r) => setSelectedId(r.id)}
        emptyTitle="No import jobs yet"
        emptyDescription="Start an import from a cPanel/WHM full-account backup to migrate a site here."
        emptyIcon={DownloadCloud}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> New import</Button>}
      />

      {/* New import */}
      <Dialog open={createOpen} onOpenChange={(v) => { setCreateOpen(v); if (!v) resetForm() }}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>New cPanel import</DialogTitle>
            <DialogDescription>
              Creates a brand-new account, then imports domains, DNS, databases, mailboxes, SSL, FTP, and cron. Provide either a file upload or a URL, not both.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={submit}>
            <DialogBody className="space-y-4">
              <FormField
                label="New Boron username"
                required
                hint="Lowercase letters and digits, starts with a letter (max 16 chars)."
                error={createMut.error?.fields?.username}
              >
                <Input
                  autoFocus
                  value={form.username}
                  onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
                  pattern="[a-z][a-z0-9]{0,15}"
                  placeholder="migrated1"
                  required
                />
              </FormField>

              <FormField label="Backup source" required>
                <Select value={source} onChange={(e) => setSource(e.target.value)}>
                  <option value="upload">Upload a tarball</option>
                  <option value="url">Download from a URL</option>
                </Select>
              </FormField>

              {source === 'upload' ? (
                <FormField label="Tarball" required hint="A cPanel/WHM full-account backup (.tar, .tar.gz, or .tgz).">
                  <Input
                    type="file"
                    accept=".tar,.tar.gz,.tgz"
                    className="h-auto py-1.5 file:mr-3 file:rounded-btn file:border-0 file:bg-muted file:px-3 file:py-1 file:text-sm file:font-medium file:text-foreground"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                  />
                </FormField>
              ) : (
                <FormField label="Backup URL" required hint="A direct link to the backup tarball.">
                  <Input
                    type="url"
                    value={form.url}
                    onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
                    placeholder="https://example.com/cpmove-olduser.tar.gz"
                  />
                </FormField>
              )}
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => { setCreateOpen(false); resetForm() }}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending} disabled={!canSubmit}>
                <DownloadCloud className="h-4 w-4" /> Start import
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Job detail */}
      <Dialog open={!!selected} onOpenChange={(v) => { if (!v) setSelectedId(null) }}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>
              Import #{selected?.id} — {selected?.username}
            </DialogTitle>
            <DialogDescription>
              {selected ? (SOURCE_LABELS[selected.source] || selected.source) : ''} source
              {selected?.started_at ? ` · started ${formatDate(selected.started_at)}` : ''}
              {selected?.completed_at ? ` · completed ${formatDate(selected.completed_at)}` : ''}
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-4">
            {selected && (
              <>
                <div className="flex items-center gap-3">
                  <StatusBadge status={selected.status} />
                  {selected.progress_message && (
                    <span className="text-sm text-muted-foreground">{selected.progress_message}</span>
                  )}
                </div>

                {selected.error && (
                  <div className="rounded-btn border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
                    {selected.error}
                  </div>
                )}

                <div>
                  <h3 className="mb-2 text-sm font-semibold text-foreground">Per-item report</h3>
                  {selected.results?.length ? (
                    <div className="max-h-80 overflow-auto rounded-btn border border-border">
                      <Table>
                        <THead>
                          <TR>
                            <TH>Item</TH>
                            <TH>Status</TH>
                            <TH>Detail</TH>
                          </TR>
                        </THead>
                        <TBody>
                          {selected.results.map((r, i) => (
                            <TR key={i}>
                              <TD className="whitespace-nowrap font-mono text-xs">{r.item}</TD>
                              <TD><StatusBadge status={r.status} /></TD>
                              <TD className="text-sm text-muted-foreground">{r.detail || '—'}</TD>
                            </TR>
                          ))}
                        </TBody>
                      </Table>
                    </div>
                  ) : (
                    <p className="flex items-center gap-2 text-sm text-muted-foreground">
                      <FileArchive className="h-4 w-4" /> No items processed yet.
                    </p>
                  )}
                </div>
              </>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setSelectedId(null)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
