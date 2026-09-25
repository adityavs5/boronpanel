import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Database, Plus, Trash2, MoreHorizontal, KeyRound, ExternalLink, Copy } from 'lucide-react'
import { get, post, patch, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatDate, copyToClipboard } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
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
  const [selected, setSelected] = useState(null)
  const [form, setForm] = useState({ name: '', password: '' })
  const [toDelete, setToDelete] = useState(null)
  const [userForm, setUserForm] = useState({ name: '', password: '' })
  const [userToDelete, setUserToDelete] = useState(null)
  const [assignUser, setAssignUser] = useState('')
  const [creds, setCreds] = useState(null) // { title, db_name, db_user, password }

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['databases', username],
    queryFn: () => get(`/api/v1/accounts/${username}/databases`),
    enabled: !!username,
  })
  const usersQuery = useQuery({
    queryKey: ['database-users', username],
    queryFn: () => get(`/api/v1/accounts/${username}/databases/users`),
    enabled: !!username,
  })
  const refreshDatabaseState = () => {
    qc.invalidateQueries({ queryKey: ['databases', username] })
    qc.invalidateQueries({ queryKey: ['database-users', username] })
  }

  const createMut = useMutation({
    mutationFn: (body) => post(`/api/v1/accounts/${username}/databases`, body),
    onSuccess: (res) => {
      toast.success('Database created', `${res?.db_name || form.name} is ready to use.`)
      refreshDatabaseState()
      if (res?.password) {
        setCreds({ title: 'Database created', db_name: res.db_name, db_user: res.db_user, password: res.password })
      }
      setForm({ name: '', password: '' })
    },
    onError: (e) => toast.error('Could not create database', e.message),
  })

  const createUserMut = useMutation({
    mutationFn: (body) => post(`/api/v1/accounts/${username}/databases/users`, body),
    onSuccess: (res) => {
      toast.success('Database user created', `${res.db_user} can now be assigned to one or more databases.`)
      refreshDatabaseState()
      setCreds({ title: 'Database user created', db_user: res.db_user, password: res.password })
      setUserForm({ name: '', password: '' })
    },
    onError: (e) => toast.error('Could not create database user', e.message),
  })

  const assignMut = useMutation({
    mutationFn: ({ database, user }) => post(`/api/v1/accounts/${username}/databases/${encodeURIComponent(database)}/users`, { user }),
    onSuccess: () => { toast.success('User assigned'); refreshDatabaseState(); setAssignUser('') },
    onError: (e) => toast.error('Could not assign user', e.message),
  })

  const revokeMut = useMutation({
    mutationFn: ({ database, user }) => del(`/api/v1/accounts/${username}/databases/${encodeURIComponent(database)}/users/${encodeURIComponent(user)}`),
    onSuccess: () => { toast.success('Database access removed'); refreshDatabaseState() },
    onError: (e) => toast.error('Could not remove database access', e.message),
  })

  const resetUserMut = useMutation({
    mutationFn: (user) => patch(`/api/v1/accounts/${username}/databases/users/${encodeURIComponent(user.db_user)}/password`, {}),
    onSuccess: (res) => setCreds({ title: 'New database user password', db_user: res.db_user, password: res.password }),
    onError: (e) => toast.error('Could not reset database user password', e.message),
  })

  const deleteUserMut = useMutation({
    mutationFn: (user) => del(`/api/v1/accounts/${username}/databases/users/${encodeURIComponent(user.db_user)}`),
    onSuccess: () => { toast.success('Database user removed'); refreshDatabaseState(); setUserToDelete(null) },
    onError: (e) => toast.error('Could not remove database user', e.message),
  })

  const resetMut = useMutation({
    mutationFn: (r) => post(`/api/v1/accounts/${username}/databases/${r.db_name}/password`, {}),
    onSuccess: (res, r) => {
      toast.success('Password reset', `A new password was generated for ${r.db_name}.`)
      refreshDatabaseState()
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
      refreshDatabaseState()
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not drop database', e.message),
  })

  const pmaMut = useMutation({
    mutationFn: ({ row }) => post(`/api/v1/accounts/${username}/databases/${row.db_name}/pma-token`, {}),
    onSuccess: (res, { popup }) => {
      if (!res?.pma_url) {
        popup?.close()
        toast.error('phpMyAdmin is not set up', 'Ask your administrator to configure phpMyAdmin access.')
        return
      }
      if (popup && !popup.closed) popup.location.replace(res.pma_url)
      else window.location.assign(res.pma_url)
    },
    onError: (e, { popup }) => { popup?.close(); toast.error('Could not open phpMyAdmin', e.message) },
  })

  function openPma(row) {
    // Open during the click gesture so slow token creation does not trigger
    // popup blocking. If the browser blocks it, use this tab after success.
    const popup = window.open('about:blank', '_blank')
    if (popup) { popup.opener = null; popup.document.title = 'Opening phpMyAdmin…' }
    pmaMut.mutate({ row, popup })
  }

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
              <DropdownMenuItem onSelect={() => openPma(r)}>
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
      <PageHeader title="Databases" description="Create and manage MySQL databases, users and application credentials." icon={Database} />
      <section className="database-create-section" aria-labelledby="database-create-title">
        <h2 id="database-create-title">Create New Database</h2>
        <p className="mb-4 text-sm text-muted-foreground">A database and its matching user are created together. Save the credentials shown after creation.</p>
        <form onSubmit={event => { event.preventDefault(); createMut.mutate({ name: form.name.trim(), password: form.password || undefined }) }}>
          <FormField label="New database" htmlFor="database-name" required hint="Enter a short suffix, such as shop. Your account prefix is added automatically.">
            <div className="database-name-input"><span aria-hidden="true">{username}_</span><Input id="database-name" value={form.name} onChange={event => setForm(current => ({ ...current, name: event.target.value }))} placeholder="shop" required aria-describedby="database-prefix" /></div>
          </FormField>
          <span id="database-prefix" className="sr-only">Database name will start with {username} underscore.</span>
          <FormField label="Database password" htmlFor="database-password" hint="Leave blank to generate a strong password.">
            <Input id="database-password" type="password" value={form.password} onChange={event => setForm(current => ({ ...current, password: event.target.value }))} autoComplete="new-password" placeholder="Generate automatically" />
          </FormField>
          <Button type="submit" loading={createMut.isPending} disabled={!form.name.trim()}>Create database</Button>
        </form>
      </section>
      <h2 className="interior-section-title">Current Databases</h2>

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
      />

      <section className="database-create-section mt-8" aria-labelledby="database-user-create-title">
        <h2 id="database-user-create-title">Create Database User</h2>
        <p className="mb-4 text-sm text-muted-foreground">Create one login, then assign it to any database on this account.</p>
        <form onSubmit={event => { event.preventDefault(); createUserMut.mutate({ name: userForm.name.trim(), password: userForm.password || undefined }) }}>
          <FormField label="New user" htmlFor="database-user-name" required hint="Your account prefix is added automatically.">
            <div className="database-name-input"><span aria-hidden="true">{username}_</span><Input id="database-user-name" value={userForm.name} onChange={event => setUserForm(current => ({ ...current, name: event.target.value }))} placeholder="reporter" required /></div>
          </FormField>
          <FormField label="User password" htmlFor="database-user-password" hint="Leave blank to generate a strong password.">
            <Input id="database-user-password" type="password" value={userForm.password} onChange={event => setUserForm(current => ({ ...current, password: event.target.value }))} autoComplete="new-password" placeholder="Generate automatically" />
          </FormField>
          <Button type="submit" loading={createUserMut.isPending} disabled={!userForm.name.trim()}>Create user</Button>
        </form>
      </section>
      <h2 className="interior-section-title">Current Database Users</h2>
      <DataTable
        columns={[
          { key: 'db_user', header: 'User', sortable: true, searchable: true, render: user => <span className="font-mono text-sm">{user.db_user}</span> },
          { key: 'databases', header: 'Database access', render: user => user.databases?.length ? user.databases.map(item => item.db_name).join(', ') : <span className="text-muted-foreground">Not assigned</span> },
          { key: 'actions', header: '', align: 'right', render: user => <div className="flex justify-end gap-2"><Button size="sm" variant="outline" onClick={() => resetUserMut.mutate(user)}>Reset password</Button><Button size="sm" variant="ghost" onClick={() => setUserToDelete(user)}>Delete</Button></div> },
        ]}
        data={usersQuery.data?.users}
        loading={usersQuery.isLoading}
        error={usersQuery.error}
        onRetry={usersQuery.refetch}
        filterable
        searchPlaceholder="Search database users…"
        getRowKey={user => user.id}
        emptyTitle="No database users yet"
        emptyDescription="Create a user and assign it to a database."
      />

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
          <DialogHeader><DialogTitle>{selected?.db_name}</DialogTitle><DialogDescription>Manage this database and the users allowed to access it.</DialogDescription></DialogHeader>
          <DialogBody className="space-y-5">
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm"><dt className="text-muted-foreground">Database</dt><dd className="break-all">{selected?.db_name}</dd><dt className="text-muted-foreground">Username</dt><dd className="break-all">{selected?.db_user}</dd><dt className="text-muted-foreground">Created</dt><dd>{selected?.created_at ? formatDate(selected.created_at) : '—'}</dd></dl>
            <div className="flex flex-wrap gap-3"><Button loading={pmaMut.isPending} onClick={() => openPma(selected)}><ExternalLink className="h-4 w-4"/> Open phpMyAdmin</Button><Button variant="outline" loading={resetMut.isPending} onClick={() => {resetMut.mutate(selected);setSelected(null)}}><KeyRound className="h-4 w-4"/> Reset password</Button></div>
            <div className="space-y-3 border-t border-border pt-4"><h3 className="font-semibold">Users with access</h3>
              {(data?.databases?.find(row => row.db_name === selected?.db_name)?.users || []).map(user => <div key={user} className="flex items-center justify-between gap-3 rounded-btn border border-border px-3 py-2 text-sm"><code>{user}</code>{user !== selected?.db_user && <Button size="sm" variant="ghost" loading={revokeMut.isPending} onClick={() => revokeMut.mutate({ database: selected.db_name, user })}>Remove</Button>}</div>)}
              <div className="flex gap-2"><Select aria-label="Database user to assign" value={assignUser} onChange={event => setAssignUser(event.target.value)}><option value="">Choose a user</option>{(usersQuery.data?.users || []).filter(user => !(data?.databases?.find(row => row.db_name === selected?.db_name)?.users || []).includes(user.db_user)).map(user => <option key={user.id} value={user.db_user}>{user.db_user}</option>)}</Select><Button disabled={!assignUser} loading={assignMut.isPending} onClick={() => assignMut.mutate({ database: selected.db_name, user: assignUser })}>Assign user</Button></div>
            </div>
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
      <ConfirmDialog
        open={!!userToDelete}
        onOpenChange={(v) => { if (!v) setUserToDelete(null) }}
        title={userToDelete ? `Delete ${userToDelete.db_user}?` : 'Delete database user?'}
        description="This login loses access to every assigned database. Databases and their data are kept. Original application users must be removed with their database."
        confirmLabel="Delete database user"
        confirmationText={userToDelete?.db_user}
        loading={deleteUserMut.isPending}
        onConfirm={() => userToDelete && deleteUserMut.mutate(userToDelete)}
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
