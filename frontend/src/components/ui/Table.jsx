import { useMemo, useState } from 'react'
import { ChevronDown, ChevronUp, ChevronsUpDown, Search, ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/cn'
import { Input } from './Input'
import { TableSkeleton } from './Skeleton'
import { EmptyState, ErrorState } from './States'

// Low-level table primitives (for hand-built tables / detail views).
export function Table({ className, ...props }) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn('w-full caption-bottom text-sm', className)} {...props} />
    </div>
  )
}
export const THead = (p) => <thead className={cn('[&_th]:border-b [&_th]:border-border', p.className)} {...p} />
export const TBody = (p) => <tbody className={cn('divide-y divide-border', p.className)} {...p} />
export const TR = ({ className, clickable, ...p }) => (
  <tr className={cn(clickable && 'cursor-pointer hover:bg-muted/60 transition-colors', className)} {...p} />
)
export const TH = ({ className, ...p }) => (
  <th className={cn('px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground', className)} {...p} />
)
export const TD = ({ className, ...p }) => <td className={cn('px-4 py-3 text-foreground align-middle', className)} {...p} />

/**
 * DataTable — sortable + filterable + paginated table with loading/empty/error
 * states built in. `columns`: [{ key, header, render?(row), sortable?, sortValue?(row),
 * searchable?, className, align }]. All processing is client-side.
 */
export function DataTable({
  columns,
  data,
  loading = false,
  error = null,
  onRetry,
  getRowKey = (row, i) => row.id ?? i,
  onRowClick,
  filterable = false,
  searchPlaceholder = 'Search…',
  pageSize = 0, // 0 = no pagination
  initialSort = null, // { key, dir }
  emptyTitle = 'Nothing here yet',
  emptyDescription,
  emptyAction,
  emptyIcon,
  className,
  toolbar,
}) {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState(initialSort)
  const [page, setPage] = useState(1)

  const searchKeys = useMemo(
    () => columns.filter((c) => c.searchable !== false && (c.searchable || c.key)).map((c) => c),
    [columns],
  )

  const filtered = useMemo(() => {
    let rows = Array.isArray(data) ? data : []
    if (filterable && query.trim()) {
      const q = query.trim().toLowerCase()
      rows = rows.filter((row) =>
        searchKeys.some((c) => {
          const v = c.searchValue ? c.searchValue(row) : row[c.key]
          return v != null && String(v).toLowerCase().includes(q)
        }),
      )
    }
    if (sort?.key) {
      const col = columns.find((c) => c.key === sort.key)
      const getVal = col?.sortValue || ((row) => row[sort.key])
      rows = [...rows].sort((a, b) => {
        const va = getVal(a)
        const vb = getVal(b)
        if (va == null) return 1
        if (vb == null) return -1
        let cmp
        if (typeof va === 'number' && typeof vb === 'number') cmp = va - vb
        else cmp = String(va).localeCompare(String(vb), undefined, { numeric: true })
        return sort.dir === 'desc' ? -cmp : cmp
      })
    }
    return rows
  }, [data, filterable, query, searchKeys, sort, columns])

  const totalPages = pageSize ? Math.max(1, Math.ceil(filtered.length / pageSize)) : 1
  const clampedPage = Math.min(page, totalPages)
  const pageRows = pageSize ? filtered.slice((clampedPage - 1) * pageSize, clampedPage * pageSize) : filtered

  function toggleSort(col) {
    if (!col.sortable) return
    setSort((prev) => {
      if (prev?.key !== col.key) return { key: col.key, dir: 'asc' }
      if (prev.dir === 'asc') return { key: col.key, dir: 'desc' }
      return null
    })
  }

  return (
    <div className={cn('rounded-card border border-border bg-card overflow-hidden', className)}>
      {(filterable || toolbar) && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
          {filterable ? (
            <div className="relative w-full max-w-xs">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value)
                  setPage(1)
                }}
                placeholder={searchPlaceholder}
                className="pl-8"
              />
            </div>
          ) : (
            <div />
          )}
          {toolbar}
        </div>
      )}

      {loading ? (
        <TableSkeleton rows={pageSize || 6} cols={columns.length} />
      ) : error ? (
        <ErrorState error={error} onRetry={onRetry} />
      ) : filtered.length === 0 ? (
        <EmptyState icon={emptyIcon} title={query ? 'No matches' : emptyTitle} description={query ? 'Try a different search.' : emptyDescription} action={!query && emptyAction} />
      ) : (
        <Table>
          <THead>
            <tr>
              {columns.map((col) => {
                const active = sort?.key === col.key
                const SortIcon = active ? (sort.dir === 'asc' ? ChevronUp : ChevronDown) : ChevronsUpDown
                return (
                  <TH key={col.key} className={cn(col.align === 'right' && 'text-right', col.headerClassName)}>
                    {col.sortable ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(col)}
                        className={cn('inline-flex items-center gap-1 hover:text-foreground transition-colors', active && 'text-foreground', col.align === 'right' && 'flex-row-reverse')}
                      >
                        {col.header}
                        <SortIcon className="h-3.5 w-3.5" />
                      </button>
                    ) : (
                      col.header
                    )}
                  </TH>
                )
              })}
            </tr>
          </THead>
          <TBody>
            {pageRows.map((row, i) => (
              <TR key={getRowKey(row, i)} clickable={!!onRowClick} onClick={onRowClick ? () => onRowClick(row) : undefined}>
                {columns.map((col) => (
                  <TD key={col.key} className={cn(col.align === 'right' && 'text-right', col.cellClassName)}>
                    {col.render ? col.render(row) : row[col.key] ?? '—'}
                  </TD>
                ))}
              </TR>
            ))}
          </TBody>
        </Table>
      )}

      {pageSize > 0 && !loading && !error && filtered.length > 0 && (
        <div className="flex items-center justify-between border-t border-border px-4 py-3 text-sm text-muted-foreground">
          <span>
            {(clampedPage - 1) * pageSize + 1}–{Math.min(clampedPage * pageSize, filtered.length)} of {filtered.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              className="rounded-btn p-1.5 hover:bg-muted disabled:opacity-40 disabled:hover:bg-transparent"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={clampedPage <= 1}
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="px-2 tabular-nums">
              {clampedPage} / {totalPages}
            </span>
            <button
              className="rounded-btn p-1.5 hover:bg-muted disabled:opacity-40 disabled:hover:bg-transparent"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={clampedPage >= totalPages}
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
