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
export const THead = (p) => (
  <thead className={cn('[&_th]:border-b [&_th]:border-border [&_tr]:bg-muted/40', p.className)} {...p} />
)
export const TBody = (p) => <tbody className={cn('divide-y divide-border', p.className)} {...p} />
export const TR = ({ className, clickable, ...p }) => (
  <tr
    className={cn('transition-colors hover:bg-muted/40', clickable && 'cursor-pointer hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring', className)}
    tabIndex={clickable ? 0 : undefined}
    {...p}
  />
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
  query: controlledQuery,
  onQueryChange,
  sort: controlledSort,
  onSortChange,
  page: controlledPage,
  onPageChange,
  emptyTitle = 'Nothing here yet',
  emptyDescription,
  emptyAction,
  emptyIcon,
  className,
  toolbar,
}) {
  const [internalQuery, setInternalQuery] = useState('')
  const [internalSort, setInternalSort] = useState(initialSort)
  const [internalPage, setInternalPage] = useState(1)
  const query = controlledQuery ?? internalQuery
  const sort = controlledSort ?? internalSort
  const page = controlledPage ?? internalPage

  function updateQuery(value) {
    if (controlledQuery === undefined) setInternalQuery(value)
    onQueryChange?.(value)
  }

  function updateSort(valueOrUpdater) {
    const value = typeof valueOrUpdater === 'function' ? valueOrUpdater(sort) : valueOrUpdater
    if (controlledSort === undefined) setInternalSort(value)
    onSortChange?.(value)
  }

  function updatePage(valueOrUpdater) {
    const value = typeof valueOrUpdater === 'function' ? valueOrUpdater(page) : valueOrUpdater
    if (controlledPage === undefined) setInternalPage(value)
    onPageChange?.(value)
  }

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
    updateSort((prev) => {
      if (prev?.key !== col.key) return { key: col.key, dir: 'asc' }
      if (prev.dir === 'asc') return { key: col.key, dir: 'desc' }
      return null
    })
    updatePage(1)
  }

  function activateRow(event, row) {
    if (!onRowClick) return
    // Let nested controls handle their own clicks and keyboard activation.
    // In particular, Space/Enter on an action must not open the row as well.
    if (event.target !== event.currentTarget &&
        (event.type === 'keydown' || event.target.closest('button, a, input, select, textarea, [role="button"], [role="menuitem"]'))) return
    if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return
    if (event.type === 'keydown') event.preventDefault()
    onRowClick(row)
  }

  const renderCell = (col, row) => col.render ? col.render(row) : row[col.key] ?? '—'

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
                  updateQuery(e.target.value)
                  updatePage(1)
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
        <>
          <div className="divide-y divide-border sm:hidden">
            {pageRows.map((row, i) => {
              const selectColumn = columns.find((col) => col.key === 'select')
              return (
                <div
                  key={getRowKey(row, i)}
                  className={cn('space-y-2.5 p-4', onRowClick && 'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring')}
                  tabIndex={onRowClick ? 0 : undefined}
                  role={onRowClick ? 'link' : undefined}
                  onClick={onRowClick ? (event) => activateRow(event, row) : undefined}
                  onKeyDown={onRowClick ? (event) => activateRow(event, row) : undefined}
                >
                  {selectColumn && (
                    <div className="flex justify-end" onClick={(event) => event.stopPropagation()}>
                      {renderCell(selectColumn, row)}
                    </div>
                  )}
                  {columns.filter((col) => col.key !== 'select').map((col) => {
                  const hasLabel = typeof col.header === 'string' && col.header.trim()
                  return (
                    <div key={col.key} className={cn(hasLabel ? 'flex items-start justify-between gap-4' : 'flex justify-end', col.cellClassName)} onClick={!hasLabel ? (e) => e.stopPropagation() : undefined}>
                      {hasLabel && <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{col.header}</span>}
                      <div className={cn('min-w-0 text-right text-sm text-foreground', hasLabel ? '' : 'w-full')}>{renderCell(col, row)}</div>
                    </div>
                  )
                  })}
                </div>
              )
            })}
          </div>
          <div className="hidden sm:block">
            <Table>
              <THead>
                <tr>
              {columns.map((col) => {
                const active = sort?.key === col.key
                const SortIcon = active ? (sort.dir === 'asc' ? ChevronUp : ChevronDown) : ChevronsUpDown
                return (
                  <TH
                    key={col.key}
                    className={cn(col.align === 'right' && 'text-right', col.headerClassName)}
                    aria-sort={col.sortable && active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                  >
                    {col.sortable ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(col)}
                        aria-label={`Sort by ${typeof col.header === 'string' ? col.header : col.key}${active ? `, currently ${sort.dir === 'asc' ? 'ascending' : 'descending'}` : ''}`}
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
              <TR key={getRowKey(row, i)} clickable={!!onRowClick} onClick={onRowClick ? (event) => activateRow(event, row) : undefined} onKeyDown={onRowClick ? (event) => activateRow(event, row) : undefined}>
                {columns.map((col) => (
                  <TD key={col.key} className={cn(col.align === 'right' && 'text-right', col.cellClassName)}>
                    {renderCell(col, row)}
                  </TD>
                ))}
              </TR>
            ))}
              </TBody>
            </Table>
          </div>
        </>
      )}

      {pageSize > 0 && !loading && !error && filtered.length > 0 && (
        <div className="flex items-center justify-between border-t border-border px-4 py-3 text-sm text-muted-foreground">
          <span>
            {(clampedPage - 1) * pageSize + 1}–{Math.min(clampedPage * pageSize, filtered.length)} of {filtered.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              aria-label="Previous page"
              className="rounded-btn p-1.5 hover:bg-muted disabled:opacity-40 disabled:hover:bg-transparent"
              onClick={() => updatePage(Math.max(1, clampedPage - 1))}
              disabled={clampedPage <= 1}
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="px-2 tabular-nums">
              {clampedPage} / {totalPages}
            </span>
            <button
              type="button"
              aria-label="Next page"
              className="rounded-btn p-1.5 hover:bg-muted disabled:opacity-40 disabled:hover:bg-transparent"
              onClick={() => updatePage(Math.min(totalPages, clampedPage + 1))}
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
