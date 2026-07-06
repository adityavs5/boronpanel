import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { LockKeyhole, Plus, Trash2 } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

export default function IpWhitelist() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ value: '', note: '' })
  const [toDelete, setToDelete] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['ip-whitelist', username],
    queryFn: () => get('/api/v1/security/ip-whitelist'),
  })

  const createMut = useMutation({
    mutationFn: (body) => post('/api/v1/security/ip-whitelist', body),
    onSuccess: () => {
      toast.success('Whitelist entry added', 'Panel login is now restricted to the whitelisted IPs.')
      qc.invalidateQueries({ queryKey: ['ip-whitelist', username] })
      setCreateOpen(false)
      setForm({ value: '', note: '' })
    },
    onError: (e) => toast.error('Could not add entry', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (r) => del(`/api/v1/security/ip-whitelist/${r.id}`),
    onSuccess: () => {
      toast.success('Whitelist entry removed', 'An empty whitelist leaves panel access unrestricted by IP.')
      qc.invalidateQueries({ queryKey: ['ip-whitelist', username] })
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not remove entry', e.message),
  })

  const columns = [
    {
      key: 'value',
      header: 'IP / CIDR',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-mono text-sm text-foreground">{r.value}</span>,
    },
    {
      key: 'note',
      header: 'Note',
      sortable: true,
      searchable: true,
      render: (r) => (r.note ? r.note : <span className="text-muted-foreground">—</span>),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => (
        <div className="flex justify-end">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Remove whitelist entry ${r.value}`}
            onClick={() => setToDelete(r)}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="IP whitelist"
        description="Restrict panel login to trusted IP addresses. Warning: an empty whitelist disables the restriction, leaving panel access open to any IP."
        icon={LockKeyhole}
      >
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Add entry
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={data?.entries}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search whitelist…"
        pageSize={15}
        getRowKey={(r) => r.id}
        emptyTitle="No whitelist entries"
        emptyDescription="Panel access is unrestricted by IP. Add an entry to lock login down to trusted addresses."
        emptyIcon={LockKeyhole}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Add entry</Button>}
      />

      {/* Add entry */}
      <Dialog open={createOpen} onOpenChange={(v) => { setCreateOpen(v); if (!v) setForm({ value: '', note: '' }) }}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Add whitelist entry</DialogTitle>
            <DialogDescription>
              Whitelist a single IP or a CIDR range that may reach the panel login.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({ value: form.value.trim(), note: form.note.trim() })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="IP or CIDR" required hint="A single address or a network range — e.g. 203.0.113.5 or 203.0.113.0/24.">
                <Input
                  autoFocus
                  value={form.value}
                  onChange={(e) => setForm((f) => ({ ...f, value: e.target.value }))}
                  placeholder="203.0.113.0/24"
                  className="font-mono text-sm"
                  required
                />
              </FormField>
              <FormField label="Note" hint="Optional label to help you remember this entry.">
                <Input
                  value={form.note}
                  onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
                  placeholder="Office network"
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending} disabled={!form.value.trim()}>Add entry</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete entry */}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => { if (!v) setToDelete(null) }}
        title={toDelete ? `Remove ${toDelete.value}?` : 'Remove whitelist entry?'}
        description="This IP or range will no longer be allowed to reach the panel login. If this is the last entry, the whitelist becomes empty and IP restriction is disabled entirely."
        confirmLabel="Remove entry"
        loading={deleteMut.isPending}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />
    </div>
  )
}
