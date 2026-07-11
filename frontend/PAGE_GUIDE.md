# Boron SPA — Page Authoring Guide (READ BEFORE WRITING A PAGE)

You are writing ONE React page component (plain JSX, React 18) for the Boron
control panel. Match the existing design system EXACTLY. Tailwind only, no inline
styles (except tiny dynamic width % on bars). Default export a component.

## Import paths (use the `@/` alias = frontend/src)
- Data: `import { get, post, patch, put, del } from '@/lib/api'`
- Account context: `import { useAccountUsername } from '@/hooks/useAccount'`  → returns the username the page acts on (customer's own, or admin's :username route param).
- Formatters: `import { formatBytes, formatMB, formatDate, formatDateShort, relativeTime, formatDuration, formatNumber, percent, titleCase, truncate, copyToClipboard } from '@/lib/utils'`
- React Query: `import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'`

## Component API (all from `@/components/ui/...`)
- `PageHeader` from '@/components/ui/PageHeader': `<PageHeader title description icon={LucideIcon}>{actions}</PageHeader>`
- Card from '@/components/ui/Card': `Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter`
- `Button` from '@/components/ui/Button': props `variant` = primary|secondary|outline|ghost|danger|success|warning|link, `size` = sm|md|lg|icon|icon-sm, `loading`, `asChild`. e.g. `<Button variant="danger" size="sm" loading={m.isPending}>…</Button>`
- Table from '@/components/ui/Table':
  - `DataTable` props: `columns, data, loading, error, onRetry, getRowKey, onRowClick, filterable, searchPlaceholder, pageSize (0=off), initialSort={key,dir}, emptyTitle, emptyDescription, emptyIcon, emptyAction, toolbar`.
    - column = `{ key, header, render?(row)=>node, sortable?, sortValue?(row), searchable?, align?: 'right', cellClassName, headerClassName }`. `data` may be a bare array or you pass `res.things`.
  - Low-level: `Table, THead, TBody, TR, TH, TD` for hand-built/detail tables.
- Inputs from '@/components/ui/Input': `Input, Textarea, Label, FormField`. `<FormField label required error hint htmlFor><Input .../></FormField>`. Input supports `invalid`.
- `Select` from '@/components/ui/Select': native styled `<Select><option/></Select>`.
- Toggles from '@/components/ui/Toggle': `Switch` (`checked`, `onCheckedChange`), `Checkbox` (`checked`, `onCheckedChange`).
- Dialog from '@/components/ui/Dialog': `Dialog, DialogContent (size sm|md|lg|xl), DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter`, and `ConfirmDialog` (`open, onOpenChange, title, description, confirmLabel, variant='danger', loading, onConfirm`). Control with `useState` + `<Dialog open onOpenChange>`.
- `Badge` (variant neutral|accent|success|warning|danger|info|outline) and `StatusBadge` (`status` string auto-colored) from '@/components/ui/...'.
- States from '@/components/ui/States': `EmptyState({icon,title,description,action})`, `ErrorState({error,onRetry,title})`.
- Skeleton from '@/components/ui/Skeleton': `Skeleton, TableSkeleton, CardSkeleton`.
- `Spinner, CenteredSpinner` from '@/components/ui/Spinner'.
- Tabs from '@/components/ui/Tabs': `Tabs, TabsList, TabsTrigger, TabsContent` (`Tabs defaultValue`, `TabsTrigger value`, `TabsContent value`).
- Progress from '@/components/ui/Progress': `ProgressBar({value,max,color,size})`, `UsageBar({label,used,total,format})`.
- `toast` from '@/components/ui/Toast': `toast.success(title, desc?)`, `toast.error(title|Error, desc?)`, `toast.warning`, `toast.info`.
- Dropdown from '@/components/ui/DropdownMenu': `DropdownMenu, DropdownMenuTrigger (asChild), DropdownMenuContent, DropdownMenuItem (destructive?), DropdownMenuLabel, DropdownMenuSeparator`.
- Icons: `import { X, Plus, Trash2, ... } from 'lucide-react'`.

## Canonical patterns

Query + states:
```jsx
const username = useAccountUsername()
const { data, isLoading, error, refetch } = useQuery({
  queryKey: ['things', username],
  queryFn: () => get(`/api/v1/accounts/${username}/things`),
  enabled: !!username,
})
const rows = data?.things || []   // most lists wrap in a key; GET /api/v1/accounts is a BARE array
```

Mutation + invalidate + toast:
```jsx
const qc = useQueryClient()
const createMut = useMutation({
  mutationFn: (body) => post(`/api/v1/accounts/${username}/things`, body),
  onSuccess: () => { toast.success('Created'); qc.invalidateQueries({ queryKey: ['things', username] }) },
  onError: (e) => toast.error('Failed', e.message),
})
```

Destructive action uses `ConfirmDialog` (never window.confirm).

List page skeleton:
```jsx
export default function Things() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const { data, isLoading, error, refetch } = useQuery({...})
  const columns = [ { key, header, render, sortable } ... ]
  return (
    <div>
      <PageHeader title="Things" description="…" icon={SomeIcon}>
        <Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4"/> Add</Button>
      </PageHeader>
      <DataTable columns={columns} data={data?.things} loading={isLoading} error={error} onRetry={refetch}
        filterable pageSize={10} emptyTitle="No things yet" emptyIcon={SomeIcon}/>
      {/* create Dialog + confirm ConfirmDialog */}
    </div>
  )
}
```

## Rules
- Every list uses `DataTable` (built-in loading/empty/error). Every form uses `FormField` with inline `error`.
- Every mutation shows a toast and invalidates its query. Destructive → `ConfirmDialog`.
- Read the referenced Jinja template (in api/templates_ui/) to mirror the fields/actions, but DO NOT copy its markup — rebuild with the components above.
- Use `useAccountUsername()` for the account — works for both customer pages and admin per-account tabs.
- Keep it self-contained in the one file. No new deps. No TypeScript.
- Money/size fields: use formatBytes/formatMB. Dates: formatDate/relativeTime.
