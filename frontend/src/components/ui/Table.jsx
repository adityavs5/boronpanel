import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronUp, ChevronsUpDown, Search, ChevronLeft, ChevronRight, Columns3 } from 'lucide-react'
import { cn } from '@/lib/cn'
import { Input } from './Input'
import { TableSkeleton } from './Skeleton'
import { EmptyState, ErrorState } from './States'
import { Button } from './Button'
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuCheckboxItem } from './DropdownMenu'

const FIT_MODES = [undefined, 'wrap', 'stack']

// Keeps every column readable when a table is wider than its space, so the
// pinned actions never cover one. Cell text wraps first; if that is not enough
// each row stacks as it does on phones. Widths are remembered per mode so the
// table returns to the roomier mode as soon as that mode fits again.
function useTableFit(resetKey) {
  const rootRef = useRef(null)
  const needed = useRef([])
  const levelRef = useRef(0)
  const [level, setLevel] = useState(0)
  const change = useCallback((next) => {
    levelRef.current = next
    setLevel(next)
  }, [])
  const measure = useCallback(() => {
    const root = rootRef.current
    if (!root || !root.clientWidth || window.matchMedia?.('(max-width: 639.98px)').matches) return
    const current = levelRef.current
    const scroll = current < 2 ? root.querySelector('.panel-table-scroll') : null
    if (scroll) {
      needed.current[current] = scroll.scrollWidth
      if (scroll.scrollWidth > scroll.clientWidth + 1) return change(current + 1)
    }
    if (current > 0 && root.clientWidth >= (needed.current[current - 1] ?? Infinity)) change(current - 1)
  }, [change])
  useLayoutEffect(() => {
    needed.current = []
    change(0)
  }, [resetKey, change])
  useLayoutEffect(measure)
  useEffect(() => {
    const root = rootRef.current
    if (!root || typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(measure)
    observer.observe(root)
    document.fonts?.ready.then(measure)
    return () => observer.disconnect()
  }, [measure])
  return { rootRef, level }
}

// Low-level table primitives (for hand-built tables / detail views).
export function Table({ className, scrollLabel = 'Scrollable table', ...props }) {
  return (
    <div className="panel-table-scroll w-full max-h-[min(65dvh,42rem)] overflow-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" role="region" aria-label={scrollLabel} tabIndex={0}>
      <table className={cn('w-full caption-bottom text-sm', className)} {...props} />
    </div>
  )
}
export const THead = (p) => (
  <thead className={cn('[&_th]:border-b [&_th]:border-border [&_tr]:bg-muted', p.className)} {...p} />
)
export const TBody = (p) => <tbody className={cn('divide-y divide-border', p.className)} {...p} />
export const TR = ({ className, clickable, ...p }) => (
  <tr
    className={cn('transition-colors duration-150 hover:bg-[rgb(var(--hover))]', clickable && 'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring', className)}
    tabIndex={clickable ? 0 : undefined}
    {...p}
  />
)
export const TH = ({ className, ...p }) => (
  <th className={cn('h-10 px-4 py-2 text-left text-[13px] font-semibold text-muted-foreground', className)} {...p} />
)
export const TD = ({ className, ...p }) => <td className={cn('h-12 px-4 py-2 text-foreground align-middle', className)} {...p} />

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
  columnPicker = false,
  defaultHiddenColumns = [],
  selectionBar,
}) {
  const [hiddenColumns, setHiddenColumns] = useState(() => new Set(defaultHiddenColumns))
  const displayedColumns = columns.filter(col => !hiddenColumns.has(col.key))
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

  const { rootRef, level: fitLevel } = useTableFit(`${displayedColumns.map(col => col.key).join()}|${pageRows.length}`)
  const selectAllControl = columns.find((col) => col.key === 'select')?.header
  const renderCell = (col, row) => col.render ? col.render(row) : row[col.key] ?? '—'

  return (
    <div ref={rootRef} data-fit={FIT_MODES[fitLevel]} className={cn('panel-data-table rounded-card border border-border bg-card overflow-hidden', className)}>
      {(filterable || toolbar || columnPicker) && (
        <div className="table-toolbar flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
          {filterable ? (
            <div className="table-search relative w-full max-w-xs">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => {
                  updateQuery(e.target.value)
                  updatePage(1)
                }}
                aria-label={searchPlaceholder}
                placeholder={searchPlaceholder}
                className="pl-8"
              />
            </div>
          ) : (
            <div />
          )}
          <div className="flex flex-wrap items-center gap-2">{toolbar}{columnPicker && <DropdownMenu><DropdownMenuTrigger asChild><Button variant="secondary" size="sm"><Columns3 className="h-4 w-4"/> Columns</Button></DropdownMenuTrigger><DropdownMenuContent>{columns.filter(col=>col.key!=='select'&&col.key!=='actions').map(col=><DropdownMenuCheckboxItem key={col.key} checked={!hiddenColumns.has(col.key)} disabled={col.key===columns.find(c=>c.key!=='select')?.key} onSelect={e=>e.preventDefault()} onCheckedChange={checked=>setHiddenColumns(prev=>{const next=new Set(prev);checked?next.delete(col.key):next.add(col.key);return next})}>{col.header}</DropdownMenuCheckboxItem>)}</DropdownMenuContent></DropdownMenu>}</div>
        </div>
      )}
      {selectionBar}

      {loading ? (
        <TableSkeleton rows={pageSize || 6} cols={columns.length} />
      ) : error ? (
        <ErrorState error={error} onRetry={onRetry} />
      ) : filtered.length === 0 ? (
        <EmptyState icon={emptyIcon} title={query ? 'No matches' : emptyTitle} description={query ? 'Try a different search.' : emptyDescription} action={!query && emptyAction} />
      ) : (
        <>
          <div className={cn('divide-y divide-border', fitLevel < 2 && 'sm:hidden')}>
            {typeof selectAllControl === 'object' && selectAllControl && <div className="table-card-select-all flex items-center gap-3 px-4 py-2.5 text-sm text-muted-foreground">{selectAllControl}<span>Select all</span></div>}
            {pageRows.map((row, i) => {
              // Phone cards: the first column is the card title, with the
              // selection checkbox before it and row actions after it.
              const selectColumn = columns.find((col) => col.key === 'select')
              const body = displayedColumns.filter((col) => col.key !== 'select')
              const titleColumn = body.find((col) => col.key !== 'actions')
              const actionsColumn = body.find((col) => col.key === 'actions')
              const rest = body.filter((col) => col !== titleColumn && col !== actionsColumn)
              return (
                <div
                  key={getRowKey(row, i)}
                  className={cn('table-card p-4', fitLevel < 2 && 'space-y-2.5', onRowClick && 'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring')}
                  tabIndex={onRowClick ? 0 : undefined}
                  role={onRowClick ? 'link' : undefined}
                  onClick={onRowClick ? (event) => activateRow(event, row) : undefined}
                  onKeyDown={onRowClick ? (event) => activateRow(event, row) : undefined}
                >
                  <div className="table-card-title flex items-center gap-3">
                    {selectColumn && <div className="flex shrink-0 items-center" onClick={(event) => event.stopPropagation()}>{renderCell(selectColumn, row)}</div>}
                    {titleColumn && <div className={cn('min-w-0 flex-1 text-[15px] font-semibold text-foreground', titleColumn.cellClassName)}>{renderCell(titleColumn, row)}</div>}
                    {actionsColumn && <div className="ml-auto flex shrink-0 items-center gap-2" onClick={(event) => event.stopPropagation()}>{renderCell(actionsColumn, row)}</div>}
                  </div>
                  {rest.map((col) => {
                  const hasLabel = typeof col.header === 'string' && col.header.trim()
                  return (
                    <div key={col.key} className={cn(hasLabel ? 'flex items-start justify-between gap-4' : 'flex justify-end', col.cellClassName)} onClick={!hasLabel ? (e) => e.stopPropagation() : undefined}>
                      {hasLabel && <span className="text-[13px] font-medium text-muted-foreground">{col.header}</span>}
                      <div className={cn('min-w-0 text-right text-sm text-foreground', hasLabel ? '' : 'w-full')}>{renderCell(col, row)}</div>
                    </div>
                  )
                  })}
                </div>
              )
            })}
          </div>
          <div className={fitLevel < 2 ? 'hidden sm:block' : 'hidden'}>
            <Table>
              <THead>
                <tr>
              {displayedColumns.map((col) => {
                const active = sort?.key === col.key
                const SortIcon = active ? (sort.dir === 'asc' ? ChevronUp : ChevronDown) : ChevronsUpDown
                return (
                  <TH
                    key={col.key}
                    className={cn(col.align === 'right' && 'text-right', col.key === 'actions' && 'table-actions-cell', col.key === 'select' && 'table-select-cell', col.headerClassName)}
                    aria-sort={col.sortable && active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                  >
                    {col.sortable ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(col)}
                        aria-label={`Sort by ${typeof col.header === 'string' ? col.header : col.key}${active ? `, currently ${sort.dir === 'asc' ? 'ascending' : 'descending'}` : ''}`}
                        className={cn('table-sort-control inline-flex items-center gap-1 hover:text-foreground transition-colors', active && 'text-foreground', col.align === 'right' && 'flex-row-reverse')}
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
                {displayedColumns.map((col) => (
                  <TD key={col.key} className={cn(col.align === 'right' && 'text-right', col.key === 'actions' && 'table-actions-cell', col.key === 'select' && 'table-select-cell', col.cellClassName)}>
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
        <div className="table-pagination flex items-center justify-between border-t border-border px-4 py-3 text-sm text-muted-foreground">
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
