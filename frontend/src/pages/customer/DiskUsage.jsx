import { useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { HardDrive, Folder, FileText, ChevronRight, RefreshCw, FolderOpen } from 'lucide-react'
import { get } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatBytes } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { DataTable } from '@/components/ui/Table'
import { ProgressBar } from '@/components/ui/Progress'
import { EmptyState, ErrorState } from '@/components/ui/States'
import { CenteredSpinner } from '@/components/ui/Spinner'

// Read-only disk usage explorer. Mirrors the daemon's fetch-on-click design
// (daemon/disktree.py): the tree endpoint returns one shallow level at a time
// (immediate subdirs + files, sorted largest-first), so clicking a directory
// drills into it by re-querying with `?path=`. Replaces disk_tree.html.

export default function DiskUsage() {
  const username = useAccountUsername()
  const [path, setPath] = useState('')

  const tree = useQuery({
    queryKey: ['disk-tree', username, path],
    queryFn: () => get(`/api/v1/accounts/${username}/disk-tree`, { params: { path } }),
    enabled: !!username,
    placeholderData: keepPreviousData,
  })

  const topFiles = useQuery({
    queryKey: ['disk-tree-top-files', username, path],
    queryFn: () => get(`/api/v1/accounts/${username}/disk-tree/top-files`, { params: { path } }),
    enabled: !!username,
    placeholderData: keepPreviousData,
  })

  const total = tree.data?.total_bytes ?? 0
  const children = tree.data?.children || []

  // Breadcrumb segments derived from the drilled-into path.
  const segments = path ? path.split('/') : []
  const drillInto = (name) => setPath(path ? `${path}/${name}` : name)
  const goToDepth = (i) => setPath(segments.slice(0, i + 1).join('/'))

  const refreshAll = () => { tree.refetch(); topFiles.refetch() }

  const fileColumns = [
    {
      key: 'path',
      header: 'Path',
      searchable: true,
      sortValue: (r) => r.path,
      render: (r) => (
        <code className="break-all font-mono text-sm text-foreground">{r.path}</code>
      ),
    },
    {
      key: 'size',
      header: 'Size',
      align: 'right',
      sortable: true,
      sortValue: (r) => r.size_bytes,
      render: (r) => <span className="tabular-nums text-foreground">{formatBytes(r.size_bytes)}</span>,
    },
  ]

  const scopeLabel = path ? `within ${path}` : 'across the whole account'

  return (
    <div>
      <PageHeader
        title="Disk Usage"
        description="Explore what is taking up space in your account, largest first."
        icon={HardDrive}
      >
        <Button variant="secondary" onClick={refreshAll} loading={tree.isFetching || topFiles.isFetching}>
          <RefreshCw className="h-4 w-4" /> Refresh
        </Button>
      </PageHeader>

      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>Largest directories</CardTitle>
            <CardDescription>
              <nav className="flex flex-wrap items-center gap-1 text-sm">
                <button
                  type="button"
                  onClick={() => setPath('')}
                  className={path ? 'text-accent-600 hover:underline dark:text-accent-400' : 'font-medium text-foreground'}
                >
                  {username} (root)
                </button>
                {segments.map((seg, i) => (
                  <span key={i} className="flex items-center gap-1">
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                    <button
                      type="button"
                      onClick={() => goToDepth(i)}
                      className={i === segments.length - 1 ? 'font-medium text-foreground' : 'text-accent-600 hover:underline dark:text-accent-400'}
                    >
                      {seg}
                    </button>
                  </span>
                ))}
                {tree.data && (
                  <span className="ml-1 text-muted-foreground">
                    — total <span className="font-medium tabular-nums text-foreground">{formatBytes(total)}</span>
                  </span>
                )}
              </nav>
            </CardDescription>
          </CardHeader>
          <CardContent>
            {tree.isLoading ? (
              <CenteredSpinner label="Scanning directory…" />
            ) : tree.error ? (
              <ErrorState error={tree.error} onRetry={tree.refetch} />
            ) : children.length === 0 ? (
              <EmptyState icon={FolderOpen} title="Empty directory" description="There is nothing stored here." />
            ) : (
              <div className="space-y-4">
                {children.map((c) => {
                  const p = total ? (c.size_bytes / total) * 100 : 0
                  const Icon = c.is_dir ? Folder : FileText
                  return (
                    <div key={c.name} className="space-y-1.5">
                      <div className="flex items-center justify-between gap-3 text-sm">
                        {c.is_dir ? (
                          <button
                            type="button"
                            onClick={() => drillInto(c.name)}
                            className="flex min-w-0 items-center gap-2 font-medium text-accent-600 hover:underline dark:text-accent-400"
                          >
                            <Icon className="h-4 w-4 shrink-0" />
                            <span className="truncate">{c.name}/</span>
                          </button>
                        ) : (
                          <span className="flex min-w-0 items-center gap-2 font-medium text-foreground">
                            <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                            <span className="truncate">{c.name}</span>
                          </span>
                        )}
                        <span className="shrink-0 tabular-nums text-muted-foreground">
                          {formatBytes(c.size_bytes)} <span className="text-muted-foreground/70">({p.toFixed(1)}%)</span>
                        </span>
                      </div>
                      <ProgressBar value={c.size_bytes} max={total || 1} />
                    </div>
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>

        <div>
          <div className="mb-3">
            <h2 className="text-base font-semibold text-foreground">Top {topFiles.data?.files?.length || ''} largest files</h2>
            <p className="text-sm text-muted-foreground">The biggest individual files {scopeLabel}.</p>
          </div>
          <DataTable
            columns={fileColumns}
            data={topFiles.data?.files}
            loading={topFiles.isLoading}
            error={topFiles.error}
            onRetry={topFiles.refetch}
            getRowKey={(r) => r.path}
            filterable
            searchPlaceholder="Search files…"
            pageSize={0}
            initialSort={{ key: 'size', dir: 'desc' }}
            emptyTitle="No files found"
            emptyDescription="This location has no files to list."
            emptyIcon={FileText}
          />
        </div>
      </div>
    </div>
  )
}
