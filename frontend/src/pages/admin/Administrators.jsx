import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Plus, ShieldCheck } from 'lucide-react'
import { get, patch, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, ConfirmDialog } from '@/components/ui/Dialog'
import { useAuth } from '@/store/auth'
import { toast } from '@/components/ui/Toast'

export default function Administrators() {
  const qc = useQueryClient()
  const actorUsername = useAuth(state => state.username)
  const [action, setAction] = useState(null)
  const [newPassword, setNewPassword] = useState('')
  const [open, setOpen] = useState(false)
  const [credentials, setCredentials] = useState(null)
  const [form, setForm] = useState({ username: '', password: '' })
  const query = useQuery({ queryKey: ['administrators'], queryFn: () => get('/api/v1/admin/administrators') })
  const create = useMutation({
    mutationFn: () => post('/api/v1/admin/administrators', { username: form.username, password: form.password || undefined }),
    onSuccess: result => { setOpen(false); setCredentials(result); setForm({ username: '', password: '' }); qc.invalidateQueries({ queryKey: ['administrators'] }) },
    onError: error => toast.error('Could not create administrator', error.message),
  })
  const status = useMutation({
    mutationFn: row => patch(`/api/v1/admin/administrators/${row.username}`, { disabled: !row.disabled }),
    onSuccess: () => { toast.success('Administrator updated'); qc.invalidateQueries({ queryKey: ['administrators'] }) },
    onError: error => toast.error('Could not update administrator', error.message),
  })
  const manage = useMutation({ mutationFn: () => post(`/api/v1/admin/administrators/${action.row.username}/actions`, { action: action.kind, ...(action.kind === 'password' ? { password: newPassword } : {}) }), onSuccess: () => { toast.success('Administrator security updated', 'Existing sessions were revoked.'); setAction(null); setNewPassword(''); qc.invalidateQueries({ queryKey: ['administrators'] }) }, onError: error => toast.error('Could not update administrator', error.message) })
  const rows = query.data?.administrators || []
  return <div>
    <PageHeader title="Administrators" description="Create separate operator logins and disable access without sharing the primary administrator password." icon={ShieldCheck}>
      <Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" />Add administrator</Button>
    </PageHeader>
    <DataTable data={rows} loading={query.isLoading} error={query.error} onRetry={query.refetch} columns={[
      { key: 'username', header: 'Username', sortable: true, render: row => <span className="font-semibold">{row.username}</span> },
      { key: 'created_at', header: 'Created', sortable: true, render: row => row.created_at ? new Date(row.created_at).toLocaleString() : '—' },
      { key: 'disabled', header: 'Status', render: row => <StatusBadge status={row.disabled ? 'disabled' : 'active'} /> },
      { key: 'two_factor_enabled', header: 'Two-factor authentication', render: row => row.two_factor_enabled ? 'Enabled' : 'Off' },
      { key: 'actions', header: '', searchable: false, render: row => <div className="flex flex-wrap gap-2"><Button size="sm" variant="secondary" disabled={row.username === actorUsername} loading={status.isPending && status.variables?.id === row.id} onClick={() => setAction({row,kind:'status'})}>{row.disabled ? 'Enable' : 'Disable'}</Button>{row.username !== actorUsername && <><Button size="sm" variant="secondary" onClick={() => { setNewPassword(''); setAction({row,kind:'password'}) }}>Reset password</Button><Button size="sm" variant="secondary" disabled={!row.two_factor_enabled} onClick={() => setAction({row,kind:'reset_2fa'})}>Reset 2FA</Button><Button size="sm" variant="ghost" className="text-danger" onClick={() => setAction({row,kind:'delete'})}>Remove</Button></>}</div> },
    ]} emptyTitle="No administrators" emptyDescription="Create an operator login for each person who administers this server." emptyIcon={ShieldCheck} />

    <ConfirmDialog open={!!action && action.kind !== 'password'} onOpenChange={value => !value && setAction(null)} title={`${action?.kind === 'delete' ? 'Remove' : action?.kind === 'reset_2fa' ? 'Reset 2FA for' : action?.row.disabled ? 'Enable' : 'Disable'} ${action?.row.username || 'administrator'}?`} description={action?.kind === 'delete' ? 'Removes this operator login. Your own login and the last enabled administrator cannot be removed. Administrators with impersonation history can be disabled instead.' : action?.kind === 'reset_2fa' ? 'Removes the authenticator and recovery codes and revokes existing sessions. The administrator must set up 2FA again.' : 'Disabled administrators cannot sign in. Disabling revokes existing sessions.'} confirmLabel="Confirm" loading={manage.isPending || status.isPending} onConfirm={() => action.kind === 'status' ? status.mutate(action.row, {onSuccess: () => setAction(null)}) : manage.mutate()} />
    <Dialog open={action?.kind === 'password'} onOpenChange={value => !value && setAction(null)}><DialogContent size="sm"><DialogHeader><DialogTitle>Reset administrator password</DialogTitle><DialogDescription>Existing sessions will be revoked. Share the new password securely.</DialogDescription></DialogHeader><form onSubmit={event => {event.preventDefault();manage.mutate()}}><DialogBody><FormField label="New password" hint="12+ characters with uppercase, lowercase, number and symbol."><Input type="password" autoComplete="new-password" required minLength={12} value={newPassword} onChange={event => setNewPassword(event.target.value)} /></FormField></DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setAction(null)}>Cancel</Button><Button type="submit" loading={manage.isPending}>Reset password</Button></DialogFooter></form></DialogContent></Dialog>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent size="sm"><DialogHeader><DialogTitle>Add administrator</DialogTitle><DialogDescription>Use an individual login for accountability. Leave the password empty to generate one.</DialogDescription></DialogHeader><form onSubmit={event => { event.preventDefault(); create.mutate() }}><DialogBody className="space-y-4"><FormField label="Username" hint="Lowercase letters and digits, maximum 16 characters."><Input autoFocus required pattern="[a-z][a-z0-9]{0,15}" autoComplete="off" value={form.username} onChange={event => setForm(value => ({ ...value, username: event.target.value }))} /></FormField><FormField label="Password" hint="Optional. Generated passwords are shown once."><Input type="password" autoComplete="new-password" value={form.password} onChange={event => setForm(value => ({ ...value, password: event.target.value }))} /></FormField></DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" loading={create.isPending}>Create administrator</Button></DialogFooter></form></DialogContent></Dialog>

    <Dialog open={!!credentials} onOpenChange={() => {}}><DialogContent size="sm" showClose={false}><DialogHeader><DialogTitle>Save the administrator login</DialogTitle><DialogDescription>The initial password is shown only now.</DialogDescription></DialogHeader><DialogBody className="space-y-3">{[['Username', credentials?.username], ['Initial password', credentials?.initial_password]].map(([label, value]) => <div key={label}><div className="mb-1 text-xs font-semibold uppercase text-muted-foreground">{label}</div><div className="flex gap-2"><Input readOnly value={value || ''} className="font-mono" /><Button type="button" variant="secondary" size="icon" aria-label={`Copy ${label}`} onClick={async () => { await navigator.clipboard.writeText(value || ''); toast.success(`${label} copied`) }}><Copy className="h-4 w-4" /></Button></div></div>)}</DialogBody><DialogFooter><Button onClick={() => setCredentials(null)}>I saved it</Button></DialogFooter></DialogContent></Dialog>
  </div>
}
