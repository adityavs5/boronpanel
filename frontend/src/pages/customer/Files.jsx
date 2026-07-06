import { useEffect, useMemo, useRef, useState, lazy, Suspense } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  FolderTree, Folder, FolderUp, File as FileIcon, FileText, Link2,
  Home, ChevronRight, Plus, Upload, Trash2, RefreshCw, Save,
  Copy, FolderInput, Archive, Search as SearchIcon, X,
} from 'lucide-react'

// Monaco is lazy-loaded so it only enters the bundle when the editor opens.
const CodeEditor = lazy(() => import('@/components/CodeEditor'))
// Extensions that get the Monaco code editor (goal: .php/.js/.css/.html/.json/.py/.env).
const CODE_EXT = /\.(php|js|jsx|mjs|cjs|ts|tsx|css|scss|less|html?|json|py|env|ini|md|xml|ya?ml|sh|sql)$/i
function isCodeFile(name) { return name === '.env' || CODE_EXT.test(name) }
import { get, post, put, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatBytes, formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, Textarea, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Badge } from '@/components/ui/Badge'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
  DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

// The read-file RPC caps at 10MB; keep the in-browser editor to a saner ceiling
// so we never pull a multi-megabyte blob into a <textarea>.
const EDIT_MAX = 2 * 1024 * 1024
const UPLOAD_MAX = 10 * 1024 * 1024 // matches the daemon's MAX_WRITE_BYTES

// path here is always relative to the account home (the router's `path` param;
// "" means the home dir itself).
function joinPath(base, name) {
  return base ? `${base}/${name}` : name
}
function parentPath(path) {
  return path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : ''
}
function entryType(e) {
  if (e.is_symlink) return 'Symlink'
  if (e.is_dir) return 'Folder'
  const dot = e.name.lastIndexOf('.')
  return dot > 0 ? e.name.slice(dot + 1).toUpperCase() : 'File'
}
function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
    reader.onerror = () => reject(new Error('Could not read the selected file'))
    reader.readAsDataURL(file)
  })
}

export default function Files() {
  const username = useAccountUsername()
  const qc = useQueryClient()

  const [path, setPath] = useState('') // relative to home; "" = home
  const [newFolderOpen, setNewFolderOpen] = useState(false)
  const [folderName, setFolderName] = useState('')
  const [deleteTarget, setDeleteTarget] = useState(null) // { name, path }
  const [editFile, setEditFile] = useState(null) // { name, path }
  const [editContent, setEditContent] = useState('')
  const uploadRef = useRef(null)
  // Phase 8 feature 13: multi-select + search + bulk ops.
  const [selected, setSelected] = useState(() => new Set())
  const [bulkDialog, setBulkDialog] = useState(null) // 'move' | 'copy' | 'zip'
  const [bulkDest, setBulkDest] = useState('')
  const [zipName, setZipName] = useState('archive')
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  const [searchTerm, setSearchTerm] = useState('')
  const [searchMode, setSearchMode] = useState('name')
  const [activeSearch, setActiveSearch] = useState(null) // { term, mode }

  const toggleSel = (p) => setSelected((prev) => {
    const next = new Set(prev)
    next.has(p) ? next.delete(p) : next.add(p)
    return next
  })
  const clearSel = () => setSelected(new Set())

  const dirKey = ['files', username, path]

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: dirKey,
    queryFn: () => get(`/api/v1/accounts/${username}/files`, { params: { path } }),
    enabled: !!username,
  })

  const rows = useMemo(() => {
    const entries = data?.entries || []
    // folders first, then files, each alphabetical
    return [...entries].sort((a, b) =>
      a.is_dir === b.is_dir ? a.name.localeCompare(b.name) : a.is_dir ? -1 : 1,
    )
  }, [data])

  const subFolders = useMemo(() => rows.filter((e) => e.is_dir), [rows])

  const crumbs = useMemo(() => {
    const segs = path ? path.split('/') : []
    return [
      { label: 'Home', path: '' },
      ...segs.map((seg, i) => ({ label: seg, path: segs.slice(0, i + 1).join('/') })),
    ]
  }, [path])

  const invalidateDir = () => qc.invalidateQueries({ queryKey: dirKey })

  // Clear multi-selection whenever we change directory.
  useEffect(() => { setSelected(new Set()) }, [path])

  // --- mutations -----------------------------------------------------------
  const mkdirMut = useMutation({
    mutationFn: (name) =>
      post(`/api/v1/accounts/${username}/files/mkdir`, null, { params: { path: joinPath(path, name) } }),
    onSuccess: (res) => {
      toast.success('Folder created', res?.path || folderName)
      invalidateDir()
      setNewFolderOpen(false)
      setFolderName('')
    },
    onError: (e) => toast.error('Could not create folder', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (target) => del(`/api/v1/accounts/${username}/files`, { params: { path: target.path } }),
    onSuccess: (_res, target) => {
      toast.success('Deleted', target.name)
      invalidateDir()
      setDeleteTarget(null)
    },
    onError: (e) => toast.error('Could not delete', e.message),
  })

  const uploadMut = useMutation({
    mutationFn: async (file) => {
      const content = await readAsBase64(file)
      return put(`/api/v1/accounts/${username}/files/content`, {
        path: joinPath(path, file.name),
        content,
        encoding: 'base64',
      })
    },
    onSuccess: (res, file) => {
      toast.success('Uploaded', `${file.name} (${formatBytes(res?.size ?? file.size)})`)
      invalidateDir()
    },
    onError: (e) => toast.error('Upload failed', e.message),
  })

  const saveMut = useMutation({
    mutationFn: ({ filePath, content }) =>
      put(`/api/v1/accounts/${username}/files/content`, { path: filePath, content, encoding: 'utf-8' }),
    onSuccess: (res) => {
      toast.success('Saved', `${res?.path} (${formatBytes(res?.size)})`)
      invalidateDir()
      qc.invalidateQueries({ queryKey: ['file-content', username, editFile?.path] })
      setEditFile(null)
    },
    onError: (e) => toast.error('Could not save file', e.message),
  })

  // --- bulk ops + search (Phase 8 feature 13) ------------------------------
  const selectedPaths = () => [...selected]
  const afterBulk = (msg) => (res) => {
    const failed = (res?.results || []).filter((r) => !r.ok)
    if (failed.length) toast.error('Some items failed', failed.map((f) => f.path).join(', '))
    else toast.success(msg)
    clearSel(); invalidateDir(); setBulkDialog(null); setBulkDeleteOpen(false)
  }
  const bulkDeleteMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${username}/files/bulk-delete`, { paths: selectedPaths() }),
    onSuccess: afterBulk('Deleted'), onError: (e) => toast.error('Bulk delete failed', e.message),
  })
  const bulkMoveMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${username}/files/bulk-move`, { paths: selectedPaths(), dest: bulkDest.trim() }),
    onSuccess: afterBulk('Moved'), onError: (e) => toast.error('Bulk move failed', e.message),
  })
  const bulkCopyMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${username}/files/bulk-copy`, { paths: selectedPaths(), dest: bulkDest.trim() }),
    onSuccess: afterBulk('Copied'), onError: (e) => toast.error('Bulk copy failed', e.message),
  })
  const zipMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${username}/files/zip`, {
      paths: selectedPaths(), archive: joinPath(path, zipName.trim()),
    }),
    onSuccess: afterBulk('Archive created'), onError: (e) => toast.error('Zip failed', e.message),
  })
  const searchQ = useQuery({
    queryKey: ['file-search', username, activeSearch?.term, activeSearch?.mode, path],
    queryFn: () => get(`/api/v1/accounts/${username}/files/search`, { params: { query: activeSearch.term, mode: activeSearch.mode, path } }),
    enabled: !!activeSearch,
  })

  // --- editor content ------------------------------------------------------
  const contentQ = useQuery({
    queryKey: ['file-content', username, editFile?.path],
    queryFn: () => get(`/api/v1/accounts/${username}/files/content`, { params: { path: editFile.path } }),
    enabled: !!editFile,
  })
  const isBinary = contentQ.data?.encoding === 'base64'
  useEffect(() => {
    if (contentQ.data) setEditContent(contentQ.data.encoding === 'utf-8' ? contentQ.data.content : '')
  }, [contentQ.data])

  // --- interactions --------------------------------------------------------
  function openEntry(row) {
    if (row.is_dir) {
      setPath(joinPath(path, row.name))
      return
    }
    if (row.size > EDIT_MAX) {
      toast.info('File is too large to open in the editor', formatBytes(row.size))
      return
    }
    setEditContent('')
    setEditFile({ name: row.name, path: joinPath(path, row.name) })
  }

  function onUploadPick(e) {
    const file = e.target.files?.[0]
    e.target.value = '' // allow re-selecting the same file
    if (!file) return
    if (file.size > UPLOAD_MAX) {
      toast.error('File too large', `${file.name} exceeds the ${formatBytes(UPLOAD_MAX)} upload limit.`)
      return
    }
    uploadMut.mutate(file)
  }

  const columns = [
    {
      key: 'select',
      header: '',
      searchable: false,
      render: (r) => (
        <input
          type="checkbox"
          checked={selected.has(joinPath(path, r.name))}
          onClick={(e) => e.stopPropagation()}
          onChange={() => toggleSel(joinPath(path, r.name))}
          aria-label={`Select ${r.name}`}
        />
      ),
    },
    {
      key: 'name',
      header: 'Name',
      sortable: true,
      searchable: true,
      render: (r) => (
        <span className="flex items-center gap-2 font-medium text-foreground">
          {r.is_dir ? (
            <Folder className="h-4 w-4 shrink-0 text-accent" />
          ) : r.is_symlink ? (
            <Link2 className="h-4 w-4 shrink-0 text-muted-foreground" />
          ) : /\.(txt|md|conf|cfg|ini|log|json|ya?ml|env|htaccess|js|jsx|ts|tsx|css|html?|php|py|sh|xml|sql)$/i.test(r.name) ? (
            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
          ) : (
            <FileIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <span className="truncate">{r.name}</span>
        </span>
      ),
    },
    {
      key: 'size',
      header: 'Size',
      align: 'right',
      sortable: true,
      sortValue: (r) => r.size,
      render: (r) => (r.is_dir ? <span className="text-muted-foreground">—</span> : formatBytes(r.size)),
    },
    {
      key: 'mtime',
      header: 'Modified',
      sortable: true,
      sortValue: (r) => r.mtime,
      render: (r) => formatDate(r.mtime * 1000),
    },
    {
      key: 'type',
      header: 'Type',
      sortable: true,
      sortValue: (r) => entryType(r),
      render: (r) => <Badge variant={r.is_dir ? 'accent' : 'neutral'}>{entryType(r)}</Badge>,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => (
        <Button
          variant="ghost"
          size="icon-sm"
          title={`Delete ${r.name}`}
          onClick={(e) => {
            e.stopPropagation()
            setDeleteTarget({ name: r.name, path: joinPath(path, r.name) })
          }}
        >
          <Trash2 className="h-4 w-4 text-danger" />
        </Button>
      ),
    },
  ]

  return (
    <div>
      <PageHeader title="File Manager" description="Browse, edit, upload, and manage your account's files." icon={FolderTree}>
        <Button variant="secondary" onClick={() => refetch()} loading={isFetching}>
          <RefreshCw className="h-4 w-4" /> Refresh
        </Button>
        <Button variant="secondary" onClick={() => uploadRef.current?.click()} loading={uploadMut.isPending}>
          <Upload className="h-4 w-4" /> Upload
        </Button>
        <Button onClick={() => { setFolderName(''); setNewFolderOpen(true) }}>
          <Plus className="h-4 w-4" /> New folder
        </Button>
        <input ref={uploadRef} type="file" className="hidden" onChange={onUploadPick} />
      </PageHeader>

      {/* Breadcrumb */}
      <nav className="mb-4 flex flex-wrap items-center gap-1 rounded-card border border-border bg-card px-4 py-2.5 text-sm">
        {crumbs.map((c, i) => (
          <span key={c.path} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="h-4 w-4 text-muted-foreground" />}
            {i === crumbs.length - 1 ? (
              <span className="flex items-center gap-1 font-medium text-foreground">
                {i === 0 && <Home className="h-4 w-4" />}
                {c.label}
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setPath(c.path)}
                className="flex items-center gap-1 rounded px-1 text-muted-foreground hover:text-foreground hover:underline"
              >
                {i === 0 && <Home className="h-4 w-4" />}
                {c.label}
              </button>
            )}
          </span>
        ))}
      </nav>

      {/* Search bar (Phase 8 f13) */}
      <form
        className="mb-4 flex flex-wrap items-center gap-2"
        onSubmit={(e) => { e.preventDefault(); if (searchTerm.trim()) setActiveSearch({ term: searchTerm.trim(), mode: searchMode }) }}
      >
        <div className="relative flex-1 min-w-[220px]">
          <SearchIcon className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input className="pl-8" value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)}
            placeholder={`Search by ${searchMode} under /${path || 'home'}…`} />
        </div>
        <Select value={searchMode} onChange={(e) => setSearchMode(e.target.value)} className="w-36">
          <option value="name">By name</option>
          <option value="content">By content</option>
        </Select>
        <Button type="submit" variant="secondary" loading={searchQ.isFetching}>Search</Button>
        {activeSearch && (
          <Button type="button" variant="ghost" onClick={() => { setActiveSearch(null); setSearchTerm('') }}>
            <X className="h-4 w-4" /> Clear
          </Button>
        )}
      </form>

      {/* Search results (Phase 8 f13) */}
      {activeSearch && (
        <Card className="mb-4">
          <CardHeader><CardTitle className="text-sm">
            Results for “{activeSearch.term}” ({activeSearch.mode}){searchQ.data?.truncated ? ' — showing first 500' : ''}
          </CardTitle></CardHeader>
          <CardContent>
            {searchQ.isLoading ? <CenteredSpinner /> : searchQ.error ? (
              <ErrorState error={searchQ.error} onRetry={searchQ.refetch} />
            ) : (searchQ.data?.results || []).length === 0 ? (
              <p className="text-sm text-muted-foreground">No matches.</p>
            ) : (
              <ul className="max-h-72 space-y-1 overflow-auto">
                {searchQ.data.results.map((r, i) => (
                  <li key={`${r.path}-${i}`} className="rounded px-2 py-1 text-sm hover:bg-muted">
                    <button type="button" className="text-left" onClick={() => { setPath(parentPath(r.path)); }}>
                      <span className="font-mono text-xs text-foreground">{r.path}</span>
                      {r.line ? <span className="ml-2 text-xs text-muted-foreground">:{r.line} {r.text}</span> : null}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}

      {/* Bulk action bar (Phase 8 f13) */}
      {selected.size > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-card border border-accent/40 bg-accent/5 px-4 py-2.5">
          <span className="text-sm font-medium text-foreground">{selected.size} selected</span>
          <Button size="sm" variant="secondary" onClick={() => { setBulkDest(''); setBulkDialog('move') }}><FolderInput className="h-4 w-4" /> Move</Button>
          <Button size="sm" variant="secondary" onClick={() => { setBulkDest(''); setBulkDialog('copy') }}><Copy className="h-4 w-4" /> Copy</Button>
          <Button size="sm" variant="secondary" onClick={() => { setZipName('archive'); setBulkDialog('zip') }}><Archive className="h-4 w-4" /> Zip</Button>
          <Button size="sm" variant="danger" onClick={() => setBulkDeleteOpen(true)}><Trash2 className="h-4 w-4" /> Delete</Button>
          <Button size="sm" variant="ghost" onClick={clearSel}>Clear</Button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-4">
        {/* LEFT: directory navigation */}
        <Card className="lg:col-span-1 h-fit">
          <CardHeader>
            <CardTitle className="text-sm">Folders</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <button
              type="button"
              onClick={() => setPath('')}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted ${path === '' ? 'font-medium text-foreground' : 'text-muted-foreground'}`}
            >
              <Home className="h-4 w-4 shrink-0" /> Home
            </button>
            {path !== '' && (
              <button
                type="button"
                onClick={() => setPath(parentPath(path))}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-muted"
              >
                <FolderUp className="h-4 w-4 shrink-0" /> Up one level
              </button>
            )}
            <div className="my-1 border-t border-border" />
            {subFolders.length === 0 ? (
              <p className="px-2 py-1.5 text-sm text-muted-foreground">No subfolders here.</p>
            ) : (
              subFolders.map((f) => (
                <button
                  key={f.name}
                  type="button"
                  onClick={() => setPath(joinPath(path, f.name))}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <Folder className="h-4 w-4 shrink-0 text-accent" />
                  <span className="truncate">{f.name}</span>
                </button>
              ))
            )}
          </CardContent>
        </Card>

        {/* RIGHT: current directory listing */}
        <div className="lg:col-span-3">
          <DataTable
            columns={columns}
            data={rows}
            loading={isLoading}
            error={error}
            onRetry={refetch}
            filterable
            searchPlaceholder="Search this folder…"
            pageSize={20}
            initialSort={{ key: 'name', dir: 'asc' }}
            getRowKey={(r) => r.name}
            onRowClick={openEntry}
            emptyTitle="Empty folder"
            emptyDescription="This directory has no files. Upload a file or create a folder to get started."
            emptyIcon={FolderTree}
          />
        </div>
      </div>

      {/* New folder dialog */}
      <Dialog open={newFolderOpen} onOpenChange={setNewFolderOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>New folder</DialogTitle>
            <DialogDescription>Create a folder in /{path || ''}</DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (folderName.trim()) mkdirMut.mutate(folderName.trim())
            }}
          >
            <DialogBody>
              <FormField label="Folder name" required hint="Created inside the current directory.">
                <Input
                  autoFocus
                  value={folderName}
                  onChange={(e) => setFolderName(e.target.value)}
                  placeholder="new-folder"
                  required
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setNewFolderOpen(false)}>Cancel</Button>
              <Button type="submit" loading={mkdirMut.isPending} disabled={!folderName.trim()}>Create</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* File editor dialog */}
      <Dialog open={!!editFile} onOpenChange={(o) => { if (!o) setEditFile(null) }}>
        <DialogContent size="xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" /> {editFile?.name}
            </DialogTitle>
            <DialogDescription>/{editFile?.path}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            {contentQ.isLoading ? (
              <CenteredSpinner />
            ) : contentQ.error ? (
              <ErrorState error={contentQ.error} onRetry={contentQ.refetch} title="Could not open file" />
            ) : isBinary ? (
              <div className="rounded-card border border-border bg-muted/40 p-6 text-center text-sm text-muted-foreground">
                This looks like a binary file ({formatBytes(contentQ.data?.size)}) and can't be edited as text.
              </div>
            ) : editFile && isCodeFile(editFile.name) ? (
              <div className="overflow-hidden rounded-card border border-border">
                <Suspense fallback={<div className="p-6"><CenteredSpinner label="Loading editor…" /></div>}>
                  <CodeEditor value={editContent} onChange={setEditContent} filename={editFile.name} height="60vh" />
                </Suspense>
              </div>
            ) : (
              <Textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                rows={20}
                spellCheck={false}
                className="font-mono text-sm"
              />
            )}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => setEditFile(null)}>Close</Button>
            <Button
              onClick={() => saveMut.mutate({ filePath: editFile.path, content: editContent })}
              loading={saveMut.isPending}
              disabled={isBinary || contentQ.isLoading || !!contentQ.error}
            >
              <Save className="h-4 w-4" /> Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(o) => { if (!o) setDeleteTarget(null) }}
        title={`Delete ${deleteTarget?.name}?`}
        description="This permanently removes the file or folder (and everything inside it). This cannot be undone."
        confirmLabel="Delete"
        variant="danger"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(deleteTarget)}
      />

      {/* Bulk move / copy (Phase 8 f13) */}
      <Dialog open={bulkDialog === 'move' || bulkDialog === 'copy'} onOpenChange={(o) => { if (!o) setBulkDialog(null) }}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>{bulkDialog === 'copy' ? 'Copy' : 'Move'} {selected.size} item(s)</DialogTitle>
            <DialogDescription>Destination folder (relative to your home).</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <FormField label="Destination folder" hint="Blank = home directory.">
              <Input autoFocus value={bulkDest} onChange={(e) => setBulkDest(e.target.value)} placeholder="public_html/backup" />
            </FormField>
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setBulkDialog(null)}>Cancel</Button>
            <Button
              loading={bulkMoveMut.isPending || bulkCopyMut.isPending}
              onClick={() => (bulkDialog === 'copy' ? bulkCopyMut : bulkMoveMut).mutate()}
            >
              {bulkDialog === 'copy' ? 'Copy here' : 'Move here'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bulk zip (Phase 8 f13) */}
      <Dialog open={bulkDialog === 'zip'} onOpenChange={(o) => { if (!o) setBulkDialog(null) }}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Zip {selected.size} item(s)</DialogTitle>
            <DialogDescription>Creates a .zip in the current folder.</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <FormField label="Archive name">
              <Input autoFocus value={zipName} onChange={(e) => setZipName(e.target.value)} placeholder="archive" />
            </FormField>
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setBulkDialog(null)}>Cancel</Button>
            <Button loading={zipMut.isPending} disabled={!zipName.trim()} onClick={() => zipMut.mutate()}>Create archive</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bulk delete confirmation (Phase 8 f13) */}
      <ConfirmDialog
        open={bulkDeleteOpen}
        onOpenChange={setBulkDeleteOpen}
        title={`Delete ${selected.size} selected item(s)?`}
        description="This permanently removes the selected files and folders. This cannot be undone."
        confirmLabel="Delete all"
        variant="danger"
        loading={bulkDeleteMut.isPending}
        onConfirm={() => bulkDeleteMut.mutate()}
      />
    </div>
  )
}
