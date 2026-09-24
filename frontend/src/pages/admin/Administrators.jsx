import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Plus, ShieldCheck } from 'lucide-react'
import { get, patch, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

export default function Administrators() {
  const qc = useQueryClient()
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
  const rows = query.data?.administrators || []
  return <div>
    <PageHeader title="Administrators" description="Create separate operator logins and disable access without sharing the primary administrator password." icon={ShieldCheck}>
      <Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" />Add administrator</Button>
    </PageHeader>
    <DataTable data={rows} loading={query.isLoading} error={query.error} onRetry={query.refetch} columns={[
      { key: 'username', header: 'Username', sortable: true, render: row => <span className="font-semibold">{row.username}</span> },
      { key: 'created_at', header: 'Created', sortable: true, render: row => row.created_at ? new Date(row.created_at).toLocaleString() : '—' },
      { key: 'disabled', header: 'Status', render: row => <StatusBadge status={row.disabled ? 'disabled' : 'active'} /> },
      { key: 'actions', header: '', searchable: false, render: row => <Button size="sm" variant="secondary" loading={status.isPending && status.variables?.id === row.id} onClick={() => status.mutate(row)}>{row.disabled ? 'Enable' : 'Disable'}</Button> },
    ]} emptyTitle="No administrators" emptyDescription="Create an operator login for each person who administers this server." emptyIcon={ShieldCheck} />

    <Dialog open={open} onOpenChange={setOpen}><DialogContent size="sm"><DialogHeader><DialogTitle>Add administrator</DialogTitle><DialogDescription>Use an individual login for accountability. Leave the password empty to generate one.</DialogDescription></DialogHeader><form onSubmit={event => { event.preventDefault(); create.mutate() }}><DialogBody className="space-y-4"><FormField label="Username" hint="Lowercase letters and digits, maximum 16 characters."><Input autoFocus required pattern="[a-z][a-z0-9]{0,15}" autoComplete="off" value={form.username} onChange={event => setForm(value => ({ ...value, username: event.target.value }))} /></FormField><FormField label="Password" hint="Optional. Generated passwords are shown once."><Input type="password" autoComplete="new-password" value={form.password} onChange={event => setForm(value => ({ ...value, password: event.target.value }))} /></FormField></DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" loading={create.isPending}>Create administrator</Button></DialogFooter></form></DialogContent></Dialog>

    <Dialog open={!!credentials} onOpenChange={() => {}}><DialogContent size="sm" showClose={false}><DialogHeader><DialogTitle>Save the administrator login</DialogTitle><DialogDescription>The initial password is shown only now.</DialogDescription></DialogHeader><DialogBody className="space-y-3">{[['Username', credentials?.username], ['Initial password', credentials?.initial_password]].map(([label, value]) => <div key={label}><div className="mb-1 text-xs font-semibold uppercase text-muted-foreground">{label}</div><div className="flex gap-2"><Input readOnly value={value || ''} className="font-mono" /><Button type="button" variant="secondary" size="icon" aria-label={`Copy ${label}`} onClick={async () => { await navigator.clipboard.writeText(value || ''); toast.success(`${label} copied`) }}><Copy className="h-4 w-4" /></Button></div></div>)}</DialogBody><DialogFooter><Button onClick={() => setCredentials(null)}>I saved it</Button></DialogFooter></DialogContent></Dialog>
  </div>
}
