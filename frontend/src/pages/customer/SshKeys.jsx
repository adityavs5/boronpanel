import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plus, Trash2 } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Textarea, FormField } from '@/components/ui/Input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

export default function SshKeys() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [keyText, setKeyText] = useState('')
  const [toDelete, setToDelete] = useState(null)
  const [selected, setSelected] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['ssh-keys', username],
    queryFn: () => get(`/api/v1/accounts/${username}/ssh-keys`),
    enabled: !!username,
  })

  const createMut = useMutation({
    mutationFn: (body) => post(`/api/v1/accounts/${username}/ssh-keys`, body),
    onSuccess: () => {
      toast.success('SSH key added', 'You can now sign in over SSH with this key.')
      qc.invalidateQueries({ queryKey: ['ssh-keys', username] })
      setCreateOpen(false)
      setKeyText('')
    },
    onError: (e) => toast.error('Could not add SSH key', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (r) => del(`/api/v1/accounts/${username}/ssh-keys/${encodeURIComponent(r.fingerprint)}`),
    onSuccess: () => {
      toast.success('SSH key removed', 'This key can no longer be used to sign in.')
      qc.invalidateQueries({ queryKey: ['ssh-keys', username] })
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not remove SSH key', e.message),
  })

  const columns = [
    {
      key: 'comment',
      header: 'Comment',
      sortable: true,
      searchable: true,
      render: (r) => (
        <button className="font-medium text-accent-600 dark:text-accent-300 hover:underline" onClick={() => setSelected(r)}>{r.comment || 'Unnamed SSH key'}</button>
      ),
    },
    {
      key: 'type',
      header: 'Type',
      sortable: true,
      searchable: true,
      render: (r) => (r.type ? <Badge variant="neutral">{r.type}</Badge> : <span className="text-muted-foreground">—</span>),
    },
    {
      key: 'bits',
      header: 'Bits',
      sortable: true,
      align: 'right',
      render: (r) => <span className="tabular-nums">{r.bits ?? '—'}</span>,
    },
    {
      key: 'fingerprint',
      header: 'Fingerprint',
      searchable: true,
      render: (r) => (
        <span className="block max-w-xs truncate font-mono text-sm text-muted-foreground" title={r.fingerprint}>
          {r.fingerprint}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => (
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={event => { event.stopPropagation(); setSelected(r) }}>Manage</Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Remove SSH key ${r.comment || r.fingerprint}`}
            onClick={event => { event.stopPropagation(); setToDelete(r) }}
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
        title="SSH keys"
        description="Add a public key to sign in over SSH as your account. Removing the last key disables SSH login again."
        icon={KeyRound}
      >
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Add SSH key
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={data?.keys}
        onRowClick={setSelected}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search SSH keys…"
        pageSize={15}
        getRowKey={(r) => r.fingerprint}
        emptyTitle="No SSH keys yet"
        emptyDescription="Add a public key to enable SSH login for this account."
        emptyIcon={KeyRound}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Add SSH key</Button>}
      />

      <Dialog open={!!selected} onOpenChange={open => !open && setSelected(null)}>
        <DialogContent size="md"><DialogHeader><DialogTitle>Manage SSH key</DialogTitle><DialogDescription>{selected?.comment || 'Unnamed SSH key'}</DialogDescription></DialogHeader>
          <DialogBody className="space-y-4"><p className="text-sm">{selected?.type || 'SSH key'} · {selected?.bits ?? '—'} bits</p>
            <FormField label="Key fingerprint"><Textarea readOnly rows={3} value={selected?.fingerprint || ''} className="font-mono text-xs"/></FormField>
            <p className="text-sm text-muted-foreground">Removing this key revokes its SSH access. Other authorized keys remain available.</p>
          </DialogBody><DialogFooter><Button variant="secondary" onClick={() => setSelected(null)}>Done</Button><Button variant="danger" onClick={() => { setToDelete(selected); setSelected(null) }}>Remove key</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      {/* Add key */}
      <Dialog open={createOpen} onOpenChange={(v) => { setCreateOpen(v); if (!v) setKeyText('') }}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Add SSH key</DialogTitle>
            <DialogDescription>
              Paste a public key — e.g. the contents of{' '}
              <code className="font-mono text-foreground">~/.ssh/id_ed25519.pub</code>. Never paste a private key.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({ key: keyText.trim() })
            }}
          >
            <DialogBody>
              <FormField label="Public key" required hint="A single line beginning with ssh-ed25519, ssh-rsa, or ecdsa-…">
                <Textarea
                  autoFocus
                  rows={5}
                  value={keyText}
                  onChange={(e) => setKeyText(e.target.value)}
                  placeholder="ssh-ed25519 AAAA… you@yourcomputer"
                  className="font-mono text-sm"
                  required
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending} disabled={!keyText.trim()}>Add SSH key</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete key */}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => { if (!v) setToDelete(null) }}
        title={toDelete ? `Remove ${toDelete.comment || 'this SSH key'}?` : 'Remove SSH key?'}
        description="This key will no longer be able to sign in over SSH. If it is the last key, SSH login is disabled for the account. This cannot be undone."
        confirmLabel="Remove SSH key"
        loading={deleteMut.isPending}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />
    </div>
  )
}
