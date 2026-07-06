import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Upload, Plus, Trash2, MoreHorizontal, KeyRound, FolderCog } from 'lucide-react'
import { get, post, patch, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatDate } from '@/lib/utils'
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

export default function Ftp() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ label: '', password: '', path: '' })
  const [pwTarget, setPwTarget] = useState(null) // row
  const [pwValue, setPwValue] = useState('')
  const [pathTarget, setPathTarget] = useState(null) // row
  const [pathValue, setPathValue] = useState('')
  const [toDelete, setToDelete] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['ftp', username],
    queryFn: () => get(`/api/v1/accounts/${username}/ftp`),
    enabled: !!username,
  })

  const createMut = useMutation({
    mutationFn: (body) => post(`/api/v1/accounts/${username}/ftp`, body),
    onSuccess: (res) => {
      toast.success('FTP account created', `${res?.ftp_login || `${username}_${form.label}`} is ready to use.`)
      qc.invalidateQueries({ queryKey: ['ftp', username] })
      setCreateOpen(false)
      setForm({ label: '', password: '', path: '' })
    },
    onError: (e) => toast.error('Could not create FTP account', e.message),
  })

  const passwordMut = useMutation({
    mutationFn: ({ label, password }) => patch(`/api/v1/accounts/${username}/ftp/${label}/password`, { password }),
    onSuccess: (_res, { row }) => {
      toast.success('Password changed', `The password for ${row.ftp_login} was updated.`)
      qc.invalidateQueries({ queryKey: ['ftp', username] })
      setPwTarget(null)
      setPwValue('')
    },
    onError: (e) => toast.error('Could not change password', e.message),
  })

  const pathMut = useMutation({
    mutationFn: ({ label, path }) => patch(`/api/v1/accounts/${username}/ftp/${label}`, { path }),
    onSuccess: (_res, { row }) => {
      toast.success('Path updated', `${row.ftp_login} is now scoped to its new path.`)
      qc.invalidateQueries({ queryKey: ['ftp', username] })
      setPathTarget(null)
      setPathValue('')
    },
    onError: (e) => toast.error('Could not change path', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (r) => del(`/api/v1/accounts/${username}/ftp/${r.label}`),
    onSuccess: (_res, r) => {
      toast.success('FTP account deleted', `${r.ftp_login} was permanently removed.`)
      qc.invalidateQueries({ queryKey: ['ftp', username] })
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not delete FTP account', e.message),
  })

  const columns = [
    {
      key: 'ftp_login',
      header: 'Login',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.ftp_login}</span>,
    },
    {
      key: 'label',
      header: 'Label',
      sortable: true,
      searchable: true,
      render: (r) => r.label || <span className="text-muted-foreground">—</span>,
    },
    {
      key: 'path',
      header: 'Path',
      searchable: true,
      render: (r) =>
        r.path
          ? <span className="font-mono text-sm text-muted-foreground">{r.path}</span>
          : <span className="text-muted-foreground">home directory</span>,
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
        <div className="flex justify-end">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.ftp_login}`}>
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onSelect={() => { setPwValue(''); setPwTarget(r) }}>
                <KeyRound className="h-4 w-4" /> Change password
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => { setPathValue(r.path || ''); setPathTarget(r) }}>
                <FolderCog className="h-4 w-4" /> Change path
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
      <PageHeader
        title="FTP accounts"
        description={`Each FTP account is chrooted to a path inside ${username || 'your'} home directory — it cannot reach anything outside it.`}
        icon={Upload}
      >
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Create FTP account
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={data?.ftp_accounts}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search FTP accounts…"
        pageSize={15}
        initialSort={{ key: 'ftp_login', dir: 'asc' }}
        getRowKey={(r) => r.id ?? r.label}
        emptyTitle="No FTP accounts yet"
        emptyDescription="Create an FTP account to give scoped file access to your home directory."
        emptyIcon={Upload}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Create FTP account</Button>}
      />

      {/* Create FTP account */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Create FTP account</DialogTitle>
            <DialogDescription>
              The login will be <code className="font-mono text-foreground">{username}_{form.label || '<label>'}</code>.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({ label: form.label.trim(), password: form.password, path: form.path.trim() })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Label" required hint="A short suffix (e.g. designer). It is prefixed with your username.">
                <Input
                  autoFocus
                  value={form.label}
                  onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
                  placeholder="designer"
                  required
                />
              </FormField>
              <FormField label="Path" hint="Path within your home directory (e.g. public_html/uploads). Leave blank for the home directory.">
                <Input
                  value={form.path}
                  onChange={(e) => setForm((f) => ({ ...f, path: e.target.value }))}
                  placeholder="public_html/uploads"
                />
              </FormField>
              <FormField label="Password" required hint="Minimum 10 characters.">
                <Input
                  type="password"
                  value={form.password}
                  onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                  placeholder="Minimum 10 characters"
                  minLength={10}
                  autoComplete="new-password"
                  required
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending} disabled={!form.label.trim() || !form.password}>
                Create FTP account
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Change password */}
      <Dialog open={!!pwTarget} onOpenChange={(v) => { if (!v) { setPwTarget(null); setPwValue('') } }}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Change password</DialogTitle>
            <DialogDescription>
              Set a new password for <code className="font-mono text-foreground">{pwTarget?.ftp_login}</code>.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (pwTarget) passwordMut.mutate({ label: pwTarget.label, password: pwValue, row: pwTarget })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="New password" required hint="Minimum 10 characters.">
                <Input
                  autoFocus
                  type="password"
                  value={pwValue}
                  onChange={(e) => setPwValue(e.target.value)}
                  placeholder="Minimum 10 characters"
                  minLength={10}
                  autoComplete="new-password"
                  required
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => { setPwTarget(null); setPwValue('') }}>Cancel</Button>
              <Button type="submit" loading={passwordMut.isPending} disabled={!pwValue}>Change password</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Change path */}
      <Dialog open={!!pathTarget} onOpenChange={(v) => { if (!v) { setPathTarget(null); setPathValue('') } }}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Change path</DialogTitle>
            <DialogDescription>
              Re-scope <code className="font-mono text-foreground">{pathTarget?.ftp_login}</code> to a path within your home directory.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (pathTarget) pathMut.mutate({ label: pathTarget.label, path: pathValue.trim(), row: pathTarget })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Path" hint="Path within your home directory (e.g. public_html/uploads). Leave blank for the home directory.">
                <Input
                  autoFocus
                  value={pathValue}
                  onChange={(e) => setPathValue(e.target.value)}
                  placeholder="public_html/uploads"
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => { setPathTarget(null); setPathValue('') }}>Cancel</Button>
              <Button type="submit" loading={pathMut.isPending}>Change path</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete */}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => { if (!v) setToDelete(null) }}
        title={toDelete ? `Delete ${toDelete.ftp_login}?` : 'Delete FTP account?'}
        description="The FTP account is permanently removed and can no longer be used to sign in. This cannot be undone."
        confirmLabel="Delete FTP account"
        loading={deleteMut.isPending}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />
    </div>
  )
}
