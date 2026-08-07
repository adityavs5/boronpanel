import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Globe, Plus, Trash2, Settings, MoreHorizontal, Copy } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'
import { toast } from '@/components/ui/Toast'

const KIND_VARIANT = { primary: 'accent', addon: 'neutral', subdomain: 'info', parked: 'warning' }

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
    onSuccess: () => { toast.success('Parked domain added'); setForm({ parked_domain: '', target_domain: '' }); setOpen(false); invalidate() },
    onError: (e) => toast.error('Could not park domain', e.message),
  })
  const deleteMut = useMutation({
    mutationFn: (row) => del(`${base}/${row.parked_domain}`),
    onSuccess: () => { toast.success('Parked domain removed'); invalidate(); setToDelete(null) },
    onError: (e) => { toast.error('Could not remove parked domain', e.message); setToDelete(null) },
  })

  const targetable = (domains || []).filter((d) => d.kind !== 'parked')
  const columns = [
    { key: 'parked_domain', header: 'Parked domain', searchable: true, render: (r) => <span className="font-medium text-foreground">{r.parked_domain}</span> },
    { key: 'target_domain', header: 'Serves', render: (r) => <span className="font-mono text-xs text-muted-foreground">{r.target_domain}</span> },
    { key: 'ssl_status', header: 'SSL', render: (r) => <StatusBadge status={r.ssl_status || 'none'} /> },
    {
      key: 'actions', header: '', align: 'right', render: (r) => (
        <Button variant="ghost" size="icon-sm" title="Remove" onClick={() => setToDelete(r)}>
          <Trash2 className="h-4 w-4 text-danger" />
        </Button>
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
        <Button size="sm" onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> Park a domain</Button>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={columns}
          data={data?.parked_domains}
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

export default function Domains() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [createOpen, setCreateOpen] = useState(false)
  const [domain, setDomain] = useState('')
  const [toDelete, setToDelete] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })

  const createMut = useMutation({
    mutationFn: (body) => post(`/api/v1/accounts/${username}/domains`, body),
    onSuccess: (d) => {
      toast.success('Domain added', `${d?.domain || domain} was added to your account.`)
      qc.invalidateQueries({ queryKey: ['domains', username] })
      setCreateOpen(false)
      setDomain('')
    },
    onError: (e) => toast.error('Could not add domain', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (d) => del(`/api/v1/accounts/${username}/domains/${d.domain}`),
    onSuccess: (_res, d) => {
      toast.success('Domain removed', `${d.domain} and its vhost were removed. Files on disk are kept.`)
      qc.invalidateQueries({ queryKey: ['domains', username] })
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not remove domain', e.message),
  })

  const columns = [
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
      render: (r) => <Badge variant={KIND_VARIANT[r.kind] || 'neutral'} className="capitalize">{r.kind}</Badge>,
    },
    {
      key: 'php_version',
      header: 'PHP',
      sortable: true,
      render: (r) => (r.php_version ? `PHP ${r.php_version}` : <span className="text-muted-foreground">Inherited</span>),
    },
    {
      key: 'ssl_status',
      header: 'SSL',
      sortable: true,
      render: (r) => <StatusBadge status={r.ssl_status || 'none'} />,
    },
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
        <div className="flex justify-end" onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.domain}`}>
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onSelect={() => navigate(`/domains/${r.domain}`)}>
                <Settings className="h-4 w-4" /> Manage
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

  return (
    <div>
      <PageHeader title="Domains" description="Domains, addon domains, and subdomains on your account." icon={Globe}>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Add domain
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={data?.domains}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search domains…"
        pageSize={15}
        initialSort={{ key: 'domain', dir: 'asc' }}
        getRowKey={(r) => r.id ?? r.domain}
        onRowClick={(r) => navigate(`/domains/${r.domain}`)}
        emptyTitle="No domains yet"
        emptyDescription="Add an addon domain or subdomain to start hosting more sites."
        emptyIcon={Globe}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Add domain</Button>}
      />

      <ParkedDomainsCard username={username} domains={data?.domains} />

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Add domain</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({ domain: domain.trim(), kind: 'addon' })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Domain" required hint="An addon domain gets its own document root and vhost.">
                <Input
                  autoFocus
                  value={domain}
                  onChange={(e) => setDomain(e.target.value)}
                  placeholder="example.com"
                  required
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending} disabled={!domain.trim()}>Add domain</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => { if (!v) setToDelete(null) }}
        title={toDelete ? `Remove ${toDelete.domain}?` : 'Remove domain?'}
        description="Its vhost and any auto-created DNS record are removed. Files on disk are kept. This cannot be undone."
        confirmLabel="Remove domain"
        confirmationText={toDelete?.domain}
        loading={deleteMut.isPending}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />
    </div>
  )
}
