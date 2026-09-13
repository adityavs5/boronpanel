import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Database, Plus, Trash2, MoreHorizontal, KeyRound, ExternalLink, Copy } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatDate, copyToClipboard } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'
import { toast } from '@/components/ui/Toast'

export default function Databases() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [selected, setSelected] = useState(null)
  const [form, setForm] = useState({ name: '', password: '' })
  const [toDelete, setToDelete] = useState(null)
  const [creds, setCreds] = useState(null) // { title, db_name, db_user, password }

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['databases', username],
    queryFn: () => get(`/api/v1/accounts/${username}/databases`),
    enabled: !!username,
  })

  const createMut = useMutation({
    mutationFn: (body) => post(`/api/v1/accounts/${username}/databases`, body),
    onSuccess: (res) => {
      toast.success('Database created', `${res?.db_name || form.name} is ready to use.`)
      qc.invalidateQueries({ queryKey: ['databases', username] })
      setCreateOpen(false)
      if (res?.password) {
        setCreds({ title: 'Database created', db_name: res.db_name, db_user: res.db_user, password: res.password })
      }
      setForm({ name: '', password: '' })
    },
    onError: (e) => toast.error('Could not create database', e.message),
  })

  const resetMut = useMutation({
    mutationFn: (r) => post(`/api/v1/accounts/${username}/databases/${r.db_name}/password`, {}),
    onSuccess: (res, r) => {
      toast.success('Password reset', `A new password was generated for ${r.db_name}.`)
      qc.invalidateQueries({ queryKey: ['databases', username] })
      setCreds({
        title: 'New password',
        db_name: res?.db_name || r.db_name,
        db_user: res?.db_user || r.db_user,
        password: res?.password,
      })
    },
    onError: (e) => toast.error('Could not reset password', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (r) => del(`/api/v1/accounts/${username}/databases/${r.db_name}`),
    onSuccess: (_res, r) => {
      toast.success('Database dropped', `${r.db_name} was permanently removed.`)
      qc.invalidateQueries({ queryKey: ['databases', username] })
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not drop database', e.message),
  })

  const pmaMut = useMutation({
    mutationFn: (r) => post(`/api/v1/accounts/${username}/databases/${r.db_name}/pma-token`, {}),
    onSuccess: (res) => {
      if (!res?.pma_url) {
        toast.error('phpMyAdmin is not set up', 'Ask your administrator to configure phpMyAdmin access.')
        return
      }
      window.open(res.pma_url, '_blank', 'noopener')
    },
    onError: (e) => toast.error('Could not open phpMyAdmin', e.message),
  })

  async function copyValue(text, label) {
    const ok = await copyToClipboard(text)
    if (ok) toast.success(`${label} copied to clipboard`)
    else toast.error('Could not copy', 'Copy the value manually.')
  }

  const columns = [
    {
      key: 'db_name',
      header: 'Database',
      sortable: true,
      searchable: true,
      render: (r) => <button type="button" className="font-medium text-accent hover:underline text-left" onClick={() => setSelected(r)} aria-label={`Manage database ${r.db_name}`}>{r.db_name}</button>,
    },
    {
      key: 'db_user',
      header: 'User',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-mono text-sm text-muted-foreground">{r.db_user}</span>,
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
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="outline" onClick={() => setSelected(r)}>Manage</Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.db_name}`}>
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onSelect={() => resetMut.mutate(r)}>
                <KeyRound className="h-4 w-4" /> Reset password
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => pmaMut.mutate(r)}>
                <ExternalLink className="h-4 w-4" /> phpMyAdmin
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setToDelete(r)}>
                <Trash2 className="h-4 w-4" /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader title="Databases" description="MySQL databases and their users on your account." icon={Database}>
        <div className="flex gap-2">
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" /> Create database
          </Button>
        </div>
      </PageHeader>
      {(data?.databases?.length ?? 0) > 0 && (
        <p className="mb-4 -mt-2 text-sm text-muted-foreground">
          Select a database to open phpMyAdmin, manage credentials, or remove it.
        </p>
      )}

      <DataTable
        columns={columns}
        data={data?.databases}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search databases…"
        pageSize={15}
        initialSort={{ key: 'db_name', dir: 'asc' }}
        getRowKey={(r) => r.id ?? r.db_name}
        emptyTitle="No databases yet"
        emptyDescription="Create a MySQL database to power your applications."
        emptyIcon={Database}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Create database</Button>}
      />

      {/* Create database */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Create database</DialogTitle>
            <DialogDescription>A MySQL database and a matching user are created together.</DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({ name: form.name.trim(), password: form.password || undefined })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Name" required hint="A short suffix (e.g. shop). It is prefixed with your username.">
                <Input
                  autoFocus
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="shop"
                  required
                />
              </FormField>
              <FormField label="Password" hint="Optional — leave blank to auto-generate a strong password.">
                <Input
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                  placeholder="Auto-generated if blank"
                  autoComplete="new-password"
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending} disabled={!form.name.trim()}>Create database</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Credentials reveal (after create / reset) */}
      <Dialog open={!!creds} onOpenChange={(v) => { if (!v) setCreds(null) }}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>{creds?.title || 'Database credentials'}</DialogTitle>
            <DialogDescription>Copy these now — the password is not shown again.</DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-3">
            {creds?.db_name && (
              <CredRow label="Database" value={creds.db_name} onCopy={() => copyValue(creds.db_name, 'Database')} />
            )}
            {creds?.db_user && (
              <CredRow label="User" value={creds.db_user} onCopy={() => copyValue(creds.db_user, 'User')} />
            )}
            {creds?.password && (
              <CredRow label="Password" value={creds.password} onCopy={() => copyValue(creds.password, 'Password')} />
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setCreds(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!selected} onOpenChange={open => !open && setSelected(null)}>
        <DialogContent size="lg">
          <DialogHeader><DialogTitle>{selected?.db_name}</DialogTitle><DialogDescription>Manage this database and its dedicated user.</DialogDescription></DialogHeader>
          <DialogBody className="space-y-5">
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm"><dt className="text-muted-foreground">Database</dt><dd className="break-all">{selected?.db_name}</dd><dt className="text-muted-foreground">Username</dt><dd className="break-all">{selected?.db_user}</dd><dt className="text-muted-foreground">Created</dt><dd>{selected?.created_at ? formatDate(selected.created_at) : '—'}</dd></dl>
            <div className="flex flex-wrap gap-3"><Button loading={pmaMut.isPending} onClick={() => pmaMut.mutate(selected)}><ExternalLink className="h-4 w-4"/> Open phpMyAdmin</Button><Button variant="outline" loading={resetMut.isPending} onClick={() => {resetMut.mutate(selected);setSelected(null)}}><KeyRound className="h-4 w-4"/> Reset password</Button></div>
          </DialogBody>
          <DialogFooter><Button variant="danger" onClick={() => {setToDelete(selected);setSelected(null)}}>Delete database</Button><Button variant="secondary" onClick={() => setSelected(null)}>Done</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete */}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => { if (!v) setToDelete(null) }}
        title={toDelete ? `Drop ${toDelete.db_name}?` : 'Drop database?'}
        description="The database and its user are permanently removed. This cannot be undone."
        confirmLabel="Drop database"
        confirmationText={toDelete?.db_name}
        loading={deleteMut.isPending}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />
    </div>
  )
}

function CredRow({ label, value, onCopy }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="flex items-center gap-2">
        <code className="flex-1 truncate rounded-btn border border-border bg-muted px-3 py-2 font-mono text-sm text-foreground">{value}</code>
        <Button variant="outline" size="icon" aria-label={`Copy ${label.toLowerCase()}`} onClick={onCopy}>
          <Copy className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}
