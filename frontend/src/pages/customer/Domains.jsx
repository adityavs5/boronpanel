import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Globe, Plus, Trash2, Settings, MoreHorizontal } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'
import { toast } from '@/components/ui/Toast'

const KIND_VARIANT = { primary: 'accent', addon: 'neutral', subdomain: 'info' }

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
        loading={deleteMut.isPending}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />
    </div>
  )
}
