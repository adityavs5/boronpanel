import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Globe, Plus, Trash2, Settings, MoreHorizontal, Copy, PauseCircle, PlayCircle, RefreshCw, Network, FolderTree } from 'lucide-react'
import { get, post, patch, del } from '@/lib/api'
import { useAuth } from '@/store/auth'
import { useAccountUsername } from '@/hooks/useAccount'
import { useDomainContext } from '@/hooks/useDomainContext'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import { StyledSelect as Select } from '@/components/ui/StyledSelect'
import { Checkbox, RadioGroup, Radio } from '@/components/ui/Toggle'
import { LongValue } from '@/components/ui/LongValue'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'
import { toast } from '@/components/ui/Toast'
import { waitForControlTask } from '@/lib/control-tasks'
import { HtaccessReport, htaccessNeedsAttention } from './HtaccessReport'

// Removal succeeds once the vhost is gone; steps after that are reported in
// cleanup_failed (for example ['DNS records', 'LSCache settings']).
const cleanupText = (items) => `These could not be cleaned up: ${items.join(', ')}. Ask your administrator to remove them.`


// Phase 8 feature 3: parked (alias) domains — serve the same docroot as a target.
function ParkedDomainsCard({ username, domains }) {
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${username}/parked-domains`
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ parked_domain: '', target_domain: '' })
  const [toDelete, setToDelete] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['parked-domains', username],
    queryFn: () => get(base),
    enabled: !!username,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['parked-domains', username] })

  const addMut = useMutation({
    mutationFn: () => post(base, { parked_domain: form.parked_domain.trim(), target_domain: form.target_domain || null }),
    onSuccess: (res) => {
      toast.success('Parked domain added', res?.dns_zone_created ? `A DNS zone was created for ${res.parked_domain}.` : undefined)
      setForm({ parked_domain: '', target_domain: '' }); setOpen(false); invalidate()
    },
    onError: (e) => toast.error('Could not park domain', e.message),
  })
  const resetAdd = addMut.reset
  useEffect(() => { if (open) resetAdd() }, [open, resetAdd])
  const deleteMut = useMutation({
    mutationFn: (row) => del(`${base}/${row.parked_domain}`),
    onSuccess: (res, row) => {
      if (res?.cleanup_failed?.length) toast.warning(`${row.parked_domain} was removed`, cleanupText(res.cleanup_failed))
      else toast.success('Parked domain removed')
      invalidate(); setToDelete(null)
    },
    onError: (e) => { toast.error('Could not remove parked domain', e.message); setToDelete(null) },
  })

  const targetable = (domains || []).filter((d) => d.kind !== 'parked')
  const columns = [
    { key: 'parked_domain', header: 'Parked domain', sortable: true, searchable: true, render: (r) => <span className="font-medium text-foreground">{r.parked_domain}</span> },
    { key: 'target_domain', header: 'Serves', sortable: true, render: (r) => <span className="font-mono text-xs text-muted-foreground">{r.target_domain}</span> },
    { key: 'effective_suspended', header: 'Website', render: (r) => <Badge title={r.suspension_reason||undefined} variant={r.effective_suspended?'warning':'success'}>{r.effective_suspended?'Suspended with target':'Active'}</Badge> },
    { key: 'ssl_status', header: 'SSL', render: (r) => <StatusBadge status={r.ssl_status || 'none'} /> },
    {
      key: 'actions', header: '', align: 'right', render: (r) => (
        <DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.parked_domain}`}><MoreHorizontal className="h-4 w-4"/></Button></DropdownMenuTrigger><DropdownMenuContent><DropdownMenuItem destructive onSelect={()=>setToDelete(r)}><Trash2 className="h-4 w-4"/>Remove</DropdownMenuItem></DropdownMenuContent></DropdownMenu>
      ),
    },
  ]

  return (
    <Card className="mt-6">
      <CardHeader className="flex flex-row items-center justify-between">
        <div>
          <CardTitle className="flex items-center gap-2"><Copy className="h-4 w-4" /> Parked (alias) domains</CardTitle>
          <CardDescription>Extra domains that serve the same site as one of your existing domains.</CardDescription>
        </div>
        <Button variant="secondary" onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> Park a domain</Button>
      </CardHeader>
      <CardContent className="card-body-flush">
        <DataTable
          columns={columns}
          data={data?.parked_domains}
          columnPicker
          filterable
          searchPlaceholder="Search parked domains…"
          loading={isLoading}
          error={error}
          onRetry={refetch}
          getRowKey={(r) => r.parked_domain}
          pageSize={10}
          emptyTitle="No parked domains"
          emptyDescription="Park a domain to point it at an existing site."
          emptyIcon={Copy}
        />
      </CardContent>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size="sm">
          <DialogHeader><DialogTitle>Park a domain</DialogTitle></DialogHeader>
          <form onSubmit={(e) => { e.preventDefault(); if (form.parked_domain.trim()) addMut.mutate() }}>
            <DialogBody className="space-y-4">
              <FormField label="Domain to park" required>
                <Input autoFocus value={form.parked_domain} placeholder="alias.com"
                  onChange={(e) => setForm((f) => ({ ...f, parked_domain: e.target.value }))} required />
              </FormField>
              <FormField label="Serves the same site as" hint="Defaults to your primary domain.">
                <Select value={form.target_domain} onChange={(e) => setForm((f) => ({ ...f, target_domain: e.target.value }))}>
                  <option value="">Primary domain</option>
                  {targetable.map((d) => <option key={d.domain} value={d.domain}>{d.domain}</option>)}
                </Select>
              </FormField>
            </DialogBody>
            <DialogFooter>
              {addMut.isPending && <p role="status" className="mr-auto text-sm text-muted-foreground">Adding parked domain…</p>}
              {!addMut.isPending && addMut.error && <p role="alert" className="mr-auto min-w-0 break-words text-sm text-danger">{addMut.error.message}</p>}
              <Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
              <Button type="submit" loading={addMut.isPending} disabled={!form.parked_domain.trim()}>Park domain</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={toDelete ? `Remove parked domain ${toDelete.parked_domain}?` : ''}
        confirmLabel="Remove"
        variant="danger"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(toDelete)}
      />
    </Card>
  )
}

export default function Domains({ subdomainsOnly = false }) {
  const admin = useAuth(state => state.role === 'admin')
  const username = useAccountUsername()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [createOpen, setCreateOpen] = useState(false)
  const [selected, setSelected] = useState(new Set())
  const [bulkAction, setBulkAction] = useState(null)
  const [bulkReport, setBulkReport] = useState([])
  const [bulkProgress, setBulkProgress] = useState('')
  const [domain, setDomain] = useState('')
  const [kind,setKind]=useState(subdomainsOnly ? 'subdomain' : 'addon')
  const validDomainName = value => value.length <= 253 && value.includes('.') && value.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))
  const domainError = domain && (kind === 'subdomain' ? !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(domain.trim()) : !validDomainName(domain.trim())) ? 'Use letters, numbers and interior hyphens; enter a domain such as example.com.' : undefined
  const [docrootMode,setDocrootMode]=useState('default')
  const [customDocroot,setCustomDocroot]=useState('')
  const [toDelete, setToDelete] = useState(null)
  const [toSuspend,setToSuspend]=useState(null)
  const [suspensionReason,setSuspensionReason]=useState('')
  const [createProgress,setCreateProgress]=useState(null)
  const [htaccessReport, setHtaccessReport] = useState(null) // {domain, report}

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })

  const parentDomains = (data?.domains || []).filter(item => item.kind !== 'subdomain' && item.kind !== 'parked')
  const [parent, setParent] = useDomainContext(username, parentDomains)

  const createMut = useMutation({
    mutationFn: async (body) => {
      setCreateProgress(null)
      const task = await post(`/api/v1/task-operations/accounts/${username}/domains`, body)
      return waitForControlTask(task, { onProgress: setCreateProgress })
    },
    onSuccess: (d) => {
      toast.success(kind === 'subdomain' ? 'Subdomain added' : 'Domain added', `${d?.domain || domain} was added to your account.`)
      qc.invalidateQueries({ queryKey: ['domains', username] })
      setCreateOpen(false)
      setDomain('')
      setCustomDocroot('')
    },
    // A 400 (invalid or reserved name) or a failed task's reason stays in
    // the dialog footer until the next attempt.
    onError: (e) => toast.error('Could not add domain', e.message),
  })
  const resetCreate = createMut.reset
  useEffect(() => { if (createOpen) resetCreate() }, [createOpen, resetCreate])
  // A name another account (or this one) already holds is a problem with the
  // name itself, so it is shown on the field rather than in the footer. The
  // 409 comes straight from the task route, or as a failed task's reason.
  const createError = !createMut.isPending ? createMut.error : null
  const nameTaken = createError && (createError.status === 409 || /already (belongs to another account|exists|in use)/i.test(createError.reason || ''))

  const deleteMut = useMutation({
    mutationFn: (d) => del(`/api/v1/accounts/${username}/domains/${d.domain}`),
    onSuccess: (res, d) => {
      if (res?.cleanup_failed?.length) toast.warning(`${d.domain} was removed`, cleanupText(res.cleanup_failed))
      else toast.success('Domain removed', `${d.domain} and its vhost were removed. Files on disk are kept.`)
      qc.invalidateQueries({ queryKey: ['domains', username] })
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not remove domain', e.message),
  })

  const suspensionMut = useMutation({
    mutationFn: (d) => patch(`/api/v1/accounts/${username}/domains/${encodeURIComponent(d.domain)}/suspension`, {
      suspended: !d.suspended, reason: d.suspended ? null : suspensionReason.trim(),
    }),
    onSuccess: (result) => {
      toast.success(result.suspended ? 'Domain suspended' : 'Domain reactivated', result.suspended ? 'Only this website now shows the suspension page.' : 'The website is serving normally again.')
      qc.invalidateQueries({ queryKey: ['domains', username] })
      setToSuspend(null);setSuspensionReason('')
    },
    onError: (e) => toast.error('Could not change domain status', e.message),
  })

  // Reloading re-renders the vhost: rewrite rules and the access rules the
  // server can enforce both take effect. The response carries the same
  // access report as GET .../htaccess-status.
  const htaccessMut = useMutation({
    mutationFn: d => post(`/api/v1/accounts/${username}/domains/${encodeURIComponent(d.domain)}/reload-htaccess`, {}),
    onSuccess: (result, d) => {
      if (htaccessNeedsAttention(result)) toast.warning('.htaccess reloaded with warnings', `${d.domain} uses its .htaccess rewrite rules and the access rules the server supports. Some lines need your attention.`)
      else toast.success('.htaccess reloaded', `${d.domain} now uses its .htaccess rewrite and access rules.`)
      if (htaccessNeedsAttention(result) || result?.access_rules?.length) setHtaccessReport({ domain: d.domain, report: result })
    },
    onError: e => toast.error('Could not reload .htaccess', e.message),
  })

  const visibleDomains = subdomainsOnly ? (data?.domains || []).filter(item => item.kind === 'subdomain') : (data?.domains || [])
  const selectedRows = visibleDomains.filter(row => selected.has(row.domain))
  useEffect(() => setSelected(previous => new Set([...previous].filter(name => (data?.domains || []).some(row => row.domain === name)))), [data?.domains])
  const bulkRows = selectedRows.filter(row => bulkAction === 'delete' ? row.kind !== 'primary' : bulkAction === 'suspend' ? !row.suspended : row.suspended)
  const bulkMut = useMutation({
    mutationFn: async () => {
      const report = []
      for (const [index, row] of bulkRows.entries()) {
        setBulkProgress(`${bulkAction === 'delete' ? 'Removing' : bulkAction === 'suspend' ? 'Suspending' : 'Reactivating'} ${row.domain} (${index + 1}/${bulkRows.length})…`)
        try {
          if (bulkAction === 'delete') {
            const result = await del(`/api/v1/accounts/${username}/domains/${row.domain}`)
            report.push({ domain: row.domain, ok: true, warning: result?.cleanup_failed?.length ? `Removed. ${cleanupText(result.cleanup_failed)}` : null })
          } else {
            await patch(`/api/v1/accounts/${username}/domains/${encodeURIComponent(row.domain)}/suspension`, { suspended: bulkAction === 'suspend', reason: bulkAction === 'suspend' ? '' : null })
            report.push({ domain: row.domain, ok: true })
          }
          setSelected(previous => new Set([...previous].filter(name => name !== row.domain)))
        } catch (error) { report.push({ domain: row.domain, ok: false, message: error.message }) }
        setBulkReport([...report])
      }
      return report
    },
    onSuccess: report => {
      qc.invalidateQueries({ queryKey: ['domains', username] })
      setBulkAction(null); setBulkProgress('')
      const failed = report.filter(row => !row.ok)
      failed.length ? toast.error('Some domains could not be changed', `${failed.length} failed. See the per-domain results below.`)
        : report.some(row => row.warning) ? toast.warning('Domains updated with warnings', 'Some cleanup steps failed. See the per-domain results below.')
          : toast.success('Domains updated', `${report.length} domains changed.`)
    },
    onError: error => toast.error('Could not update selected domains', error.message),
  })

  const columns = [
    { key: 'select', header: <Checkbox aria-label="Select all domains" checked={selectedRows.length === visibleDomains.length && visibleDomains.length > 0 ? true : selectedRows.length ? 'indeterminate' : false} onCheckedChange={checked => setSelected(checked ? new Set(visibleDomains.map(row => row.domain)) : new Set())} />, searchable: false,
      render: row => <Checkbox aria-label={`Select ${row.domain}`} checked={selected.has(row.domain)} onClick={e => e.stopPropagation()} onCheckedChange={checked => setSelected(previous => { const next = new Set(previous); checked ? next.add(row.domain) : next.delete(row.domain); return next })} /> },
    {
      key: 'domain',
      header: 'Domain',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.domain}</span>,
    },
    {
      key: 'kind',
      header: 'Kind',
      sortable: true,
      cellClassName: 'domain-short-value',
      render: (r) => <span className="capitalize">{r.kind}</span>,
    },
    {
      key: 'php_version',
      header: 'PHP',
      sortable: true,
      cellClassName: 'domain-short-value',
      render: (r) => (r.php_version ? `PHP ${r.php_version}` : <span className="text-muted-foreground">Inherited</span>),
    },
    {
      key: 'ssl_status',
      header: 'SSL',
      sortable: true,
      render: (r) => <StatusBadge status={r.ssl_status || 'none'} />,
    },
    {
      key: 'suspended',
      header: 'Website',
      sortable: true,
      render: (r) => <Badge title={r.suspension_reason || undefined} variant={r.suspended ? 'warning' : 'success'}>{r.suspended ? 'Suspended' : 'Active'}</Badge>,
    },
    { key:'docroot', header:'Document root', sortable:true, render:row=><LongValue value={row.docroot} label={`${row.domain} document root`}/> },
    {
      key: 'created_at',
      header: 'Created',
      sortable: true,
      render: (r) => (r.created_at ? formatDate(r.created_at) : '—'),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => (
        <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
          <Button variant="secondary" size="sm" onClick={() => navigate(admin ? `/accounts/${username}/domains/${r.domain}` : `/domains/${r.domain}`)}>Manage</Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.domain}`}>
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onSelect={() => navigate(admin ? `/accounts/${username}/domains/${r.domain}` : `/domains/${r.domain}`)}>
                <Settings className="h-4 w-4" /> Manage
              </DropdownMenuItem>
              <DropdownMenuItem disabled={suspensionMut.isPending} onSelect={() => {setToSuspend(r);setSuspensionReason(r.suspension_reason||'')}}>
                {r.suspended ? <PlayCircle className="h-4 w-4" /> : <PauseCircle className="h-4 w-4" />}
                {r.suspended ? 'Unsuspend website' : 'Suspend website'}
              </DropdownMenuItem>
              <DropdownMenuItem disabled={htaccessMut.isPending} onSelect={() => htaccessMut.mutate(r)}>
                <RefreshCw className="h-4 w-4" /> Reload .htaccess
              </DropdownMenuItem>
              {r.kind !== 'primary' && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem destructive onSelect={() => setToDelete(r)}>
                    <Trash2 className="h-4 w-4" /> Delete
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  ]

  const fullSubdomain = `${domain.trim() || 'subdomain'}.${parent || 'example.com'}`
  const parentDocroot = parentDomains.find((item) => item.domain === parent)?.docroot || `/home/${username}/public_html`

  return (
    <div className="reference-page">
      <PageHeader title={subdomainsOnly ? 'Subdomains' : 'Domains'} description={subdomainsOnly ? 'Create subdomains under a domain you own and choose where their files live.' : 'Primary, addon, and parked domains on your account.'} icon={Globe} primaryAction={<Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> {subdomainsOnly ? 'Add subdomain' : 'Add domain'}
        </Button>} />

      <DataTable
        className="domains-reference-table"
        columns={columns}
        data={visibleDomains}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        columnPicker
        defaultHiddenColumns={admin ? ['docroot', 'created_at'] : ['created_at']}
        selectionBar={selectedRows.length > 0 && <div className="table-bulk-bar" aria-label="Selected domain actions"><strong>{selectedRows.length} selected</strong><span>With selected:</span>{selectedRows.length < visibleDomains.length && <Button variant="ghost" size="sm" disabled={bulkMut.isPending} onClick={() => setSelected(new Set(visibleDomains.map(row => row.id ?? row.domain)))}>Select all {visibleDomains.length}</Button>}<Button variant="secondary" size="sm" disabled={bulkMut.isPending || selectedRows.every(row => row.suspended)} onClick={() => setBulkAction('suspend')}>Suspend</Button><Button variant="secondary" size="sm" disabled={bulkMut.isPending || selectedRows.every(row => !row.suspended)} onClick={() => setBulkAction('unsuspend')}>Unsuspend</Button><Button variant="secondary" size="sm" disabled={bulkMut.isPending || selectedRows.every(row => row.kind === 'primary')} onClick={() => setBulkAction('delete')}>Delete</Button><Button variant="ghost" size="sm" disabled={bulkMut.isPending} onClick={() => setSelected(new Set())}>Clear</Button></div>}
        searchPlaceholder="Search domains…"
        pageSize={15}
        initialSort={{ key: 'domain', dir: 'asc' }}
        getRowKey={(r) => r.id ?? r.domain}
        onRowClick={(r) => navigate(admin ? `/accounts/${username}/domains/${r.domain}` : `/domains/${r.domain}`)}
        emptyTitle={subdomainsOnly ? 'No subdomains yet' : 'No domains yet'}
        emptyDescription={subdomainsOnly ? 'Create a subdomain with its own site root and DNS address.' : 'Add a domain to start hosting another site.'}
        emptyIcon={Globe}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> {subdomainsOnly ? 'Add subdomain' : 'Add domain'}</Button>}
      />

      {bulkReport.length > 0 && <section className="tool-section" aria-label="Bulk domain results"><div className="tool-section-heading"><h2>Domain changes</h2></div>{bulkReport.map(row => <div key={row.domain} className="settings-inline-action"><strong>{row.domain}</strong><span className={!row.ok ? 'text-danger' : row.warning ? 'text-warning' : 'text-success'}>{!row.ok ? row.message : row.warning || 'Updated'}</span></div>)}</section>}
      <Dialog open={!!htaccessReport} onOpenChange={open => { if (!open) setHtaccessReport(null) }}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle className="break-all">.htaccess for {htaccessReport?.domain}</DialogTitle>
            <DialogDescription>Rewrite rules are applied as written. These are the access rules the server enforces for this website.</DialogDescription>
          </DialogHeader>
          <DialogBody>{htaccessReport && <HtaccessReport report={htaccessReport.report} />}</DialogBody>
          <DialogFooter><Button variant="secondary" onClick={() => setHtaccessReport(null)}>Done</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog open={!!bulkAction} onOpenChange={open => !open && !bulkMut.isPending && setBulkAction(null)} title={`${bulkAction === 'delete' ? 'Remove' : bulkAction === 'suspend' ? 'Suspend' : 'Unsuspend'} selected domains?`} description={<><p>{bulkRows.map(row => row.domain).join(', ')}</p><p>{bulkAction === 'delete' ? 'Primary domains are retained. Domain configuration is removed; files on disk are kept.' : 'Only these websites change status. Account files are retained.'}</p>{bulkProgress && <p role="status">{bulkProgress}</p>}</>} confirmationText={username} confirmLabel={bulkAction === 'delete' ? 'Remove domains' : bulkAction === 'suspend' ? 'Suspend domains' : 'Unsuspend domains'} loading={bulkMut.isPending} onConfirm={() => { setBulkReport([]); bulkMut.mutate() }} />
      {!subdomainsOnly && <ParkedDomainsCard username={username} domains={data?.domains} />}

      <Dialog open={createOpen} onOpenChange={open => { if (!createMut.isPending) setCreateOpen(open) }}>
        <DialogContent
          size={kind === 'subdomain' ? 'lg' : 'sm'}
          className={kind === 'subdomain' ? 'subdomain-dialog' : undefined}
          showClose={!createMut.isPending}
          onEscapeKeyDown={event => { if (createMut.isPending) event.preventDefault() }}
          onPointerDownOutside={event => { if (createMut.isPending) event.preventDefault() }}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">{kind === 'subdomain' && <Network className="h-5 w-5 text-accent" />}{kind === 'subdomain' ? 'Add New Subdomain' : 'Add domain'}</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (domainError || !domain.trim() || (kind === 'subdomain' && !parent)) return
              createMut.mutate({ domain: kind==='subdomain'?`${domain.trim()}.${parent}`:domain.trim(), kind, parent_domain: kind === 'subdomain' ? parent : undefined, document_root_mode: kind === 'subdomain' ? docrootMode : 'default', document_root: kind === 'subdomain' && docrootMode === 'custom' ? customDocroot.trim() : undefined })
            }}
          >
            <DialogBody className="space-y-4">
              {!subdomainsOnly && <FormField label="Site type" htmlFor="domain-kind"><Select id="domain-kind" value={kind} onChange={e=>{setKind(e.target.value);setDomain('');setParent(parentDomains[0]?.domain||'')}}><option value="addon">Domain</option><option value="subdomain" disabled={!parentDomains.length}>Subdomain</option></Select></FormField>}
              <FormField error={domainError || (nameTaken ? createError.message : undefined)} label={kind==='subdomain'?'Subdomain':'Domain'} htmlFor="new-domain-name" required hint={kind==='subdomain'?'Enter the new name on the left. The selected parent domain remains visible beside it.':'Enter a complete domain name.'}>
                {kind === 'subdomain' ? <div className="subdomain-address-composer">
                  <Input id="new-domain-name" autoFocus value={domain} onChange={(e) => { setDomain(e.target.value); if (nameTaken) resetCreate() }} placeholder="blog" required className="subdomain-label-input" autoComplete="off" spellCheck={false} />
                  <div className="subdomain-parent-control"><span aria-hidden="true">.</span><Select id="subdomain-parent" aria-label="Parent domain" value={parent} onChange={e=>setParent(e.target.value)}>
                    {parentDomains.map(d=><option key={d.domain} value={d.domain}>{d.domain}</option>)}
                  </Select></div>
                </div> : <Input id="new-domain-name" autoFocus value={domain} onChange={(e) => { setDomain(e.target.value); if (nameTaken) resetCreate() }} placeholder="example.com" required />}
              </FormField>
              {kind==='subdomain'&&<div className="subdomain-address-preview"><Globe aria-hidden="true" /><span>Full address</span><strong>{fullSubdomain}</strong></div>}
              {kind==='subdomain'&&<fieldset className="subdomain-root-options"><legend>Document root</legend><RadioGroup value={docrootMode} onValueChange={setDocrootMode} aria-label="Document root" className="subdomain-root-options">
                <label className={docrootMode==='default'?'selected':''}><Radio aria-label="Separate website folder" value="default"/><span><strong>Separate website folder</strong><small>Recommended for an independent website</small><code>/home/{username}/{fullSubdomain}/public_html</code></span></label>
                <label className={docrootMode==='parent'?'selected':''}><Radio aria-label="Use the parent website" value="parent"/><span><strong>Use the parent website</strong><small>Serve the same files as {parent || 'the parent domain'}</small><code>{parentDocroot}</code></span></label>
                <label className={docrootMode==='custom'?'selected':''}><Radio aria-label="Choose a custom folder" value="custom"/><span><strong>Choose a custom folder</strong><small>Use an account-relative document root</small></span></label>
              </RadioGroup></fieldset>}
              {kind==='subdomain'&&docrootMode==='custom'&&<FormField label="Account-relative folder" htmlFor="subdomain-custom-docroot" hint={`Stored inside /home/${username}.`} required><Input id="subdomain-custom-docroot" value={customDocroot} onChange={e=>setCustomDocroot(e.target.value)} placeholder="sites/blog/public_html" required /></FormField>}
              {kind==='subdomain'?<div className="subdomain-dns-note"><FolderTree aria-hidden="true" /><p><strong>DNS is configured automatically.</strong><span>The address record is added through the parent zone’s active provider, including Cloudflare.</span></p></div>:<p className="text-sm text-muted-foreground">A DNS zone and website address records are added automatically.</p>}
            </DialogBody>
            <DialogFooter>
              <div className="mr-auto min-w-0 text-sm">
                {createMut.isPending && <p role="status" className="text-muted-foreground">{createProgress?.progress_message || `Adding ${kind === 'subdomain' ? fullSubdomain : domain.trim()}… this can take up to 30 seconds. Do not close this page.`}</p>}
                {createError && !nameTaken && <p role="alert" className="text-danger">{createError.message}</p>}
              </div>
              <Button type="button" variant="secondary" disabled={createMut.isPending} onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending} disabled={!!domainError || !domain.trim() || (kind === 'subdomain' && !parent)}>{kind === 'subdomain' ? 'Add subdomain' : 'Add domain'}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => { if (!v) { setToDelete(null); deleteMut.reset() } }}
        title={toDelete ? `Remove ${toDelete.domain}?` : 'Remove domain?'}
        description="Its vhost and any auto-created DNS record are removed. Files on disk are kept. This cannot be undone."
        confirmLabel="Remove domain"
        confirmationText={toDelete?.domain}
        loading={deleteMut.isPending}
        error={deleteMut.error}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />

      <Dialog open={!!toSuspend} onOpenChange={open=>{if(!open&&!suspensionMut.isPending){setToSuspend(null);setSuspensionReason('')}}}>
        <DialogContent size="sm"><DialogHeader><DialogTitle>{toSuspend?.suspended?'Reactivate website':'Suspend website'}</DialogTitle></DialogHeader>
          <form onSubmit={event=>{event.preventDefault();if(toSuspend)suspensionMut.mutate(toSuspend)}}><DialogBody className="space-y-4">
            <p className="text-sm text-muted-foreground">{toSuspend?.suspended?'The website will begin serving normally again. Account-level restrictions still take precedence.':'Only this website will show the configured suspension page. Mail, DNS, databases, and files remain available.'}</p>
            {!toSuspend?.suspended&&<FormField label="Reason" hint="Shown in the domain status for administrators and this account."><Input autoFocus maxLength={500} value={suspensionReason} onChange={event=>setSuspensionReason(event.target.value)} placeholder="For example, site maintenance"/></FormField>}
          </DialogBody><DialogFooter><Button type="button" variant="secondary" disabled={suspensionMut.isPending} onClick={()=>setToSuspend(null)}>Cancel</Button><Button type="submit" variant={toSuspend?.suspended?'primary':'danger'} loading={suspensionMut.isPending}>{toSuspend?.suspended?'Reactivate website':'Suspend website'}</Button></DialogFooter></form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
