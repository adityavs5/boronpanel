import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Pencil, Pause, Play, Plus, Store, Trash2, Users } from 'lucide-react'
import { del, get, patch, post, put } from '@/lib/api'
import { useAuth } from '@/store/auth'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, ConfirmDialog } from '@/components/ui/Dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { toast } from '@/components/ui/Toast'

const blankPlan = { name: '', max_accounts: 10, max_total_disk_mb: 102400, account_quota_soft_mb: 4096, account_quota_hard_mb: 5120, account_cpu_cores: 0.5, account_mem_mb: 1024, account_io_mb: 50, account_pids_max: 100, php_version: '8.3' }

function Credentials({ result, onClose }) {
  if (!result) return null
  const copy = async () => { try { await navigator.clipboard.writeText(result.initial_password); toast.success('Password copied') } catch { toast.error('Could not copy') } }
  return <Dialog open onOpenChange={value => !value && onClose()}><DialogContent size="sm"><DialogHeader><DialogTitle>Save the new login</DialogTitle><DialogDescription>This password is shown once. Use these credentials to sign in as the new panel user.</DialogDescription></DialogHeader><DialogBody className="space-y-3"><FormField label="Username" htmlFor="issued-username"><Input id="issued-username" readOnly value={result.username} /></FormField><FormField label="Initial password" htmlFor="issued-password"><div className="flex gap-2"><Input id="issued-password" readOnly className="font-mono" value={result.initial_password} /><Button variant="secondary" onClick={copy}><Copy className="h-4 w-4" />Copy</Button></div></FormField></DialogBody><DialogFooter><Button onClick={onClose}>I saved it</Button></DialogFooter></DialogContent></Dialog>
}

function AdminResellers() {
  const qc = useQueryClient()
  const [planOpen, setPlanOpen] = useState(false)
  const [editingPlan, setEditingPlan] = useState(null)
  const [deletingPlan, setDeletingPlan] = useState(null)
  const [resellerOpen, setResellerOpen] = useState(false)
  const [credentials, setCredentials] = useState(null)
  const [plan, setPlan] = useState(blankPlan)
  const [reseller, setReseller] = useState({ username: '', company: '', plan_id: '', password: '' })
  const plans = useQuery({ queryKey: ['reseller-plans'], queryFn: () => get('/api/v1/admin/resellers/plans') })
  const resellers = useQuery({ queryKey: ['resellers'], queryFn: () => get('/api/v1/admin/resellers') })
  const refresh = () => { qc.invalidateQueries({ queryKey: ['reseller-plans'] }); qc.invalidateQueries({ queryKey: ['resellers'] }) }
  const planPayload = () => ({...Object.fromEntries(Object.entries(plan).filter(([key]) => key !== 'account_cpu_cores').map(([key, value]) => [key, key === 'name' || key === 'php_version' ? value : Number(value)])), account_cpu_pct: Math.round(Number(plan.account_cpu_cores) * 100)})
  const savePlan = useMutation({ mutationFn: () => editingPlan ? put(`/api/v1/admin/resellers/plans/${editingPlan.id}`, planPayload()) : post('/api/v1/admin/resellers/plans', planPayload()), onSuccess: () => { toast.success(editingPlan ? 'Reseller plan updated' : 'Reseller plan created'); setPlanOpen(false); setEditingPlan(null); setPlan(blankPlan); refresh() }, onError: error => toast.error('Could not save plan', error.message) })
  const removePlan = useMutation({ mutationFn: row => del(`/api/v1/admin/resellers/plans/${row.id}`), onSuccess: () => { toast.success('Reseller plan deleted'); setDeletingPlan(null); refresh() }, onError: error => toast.error('Could not delete plan', error.message) })
  const createReseller = useMutation({ mutationFn: () => post('/api/v1/admin/resellers', { ...reseller, plan_id: Number(reseller.plan_id), password: reseller.password || undefined, company: reseller.company || undefined }), onSuccess: result => { setCredentials(result); setResellerOpen(false); setReseller({ username: '', company: '', plan_id: '', password: '' }); refresh() }, onError: error => toast.error('Could not create reseller', error.message) })
  const updateReseller = useMutation({ mutationFn: ({ row, changes }) => patch(`/api/v1/admin/resellers/${row.id}`, changes), onSuccess: () => { toast.success('Reseller updated'); refresh() }, onError: error => toast.error('Could not update reseller', error.message) })
  const planRows = plans.data?.plans || []
  const openNewPlan = () => { setEditingPlan(null); setPlan(blankPlan); setPlanOpen(true) }
  const openEditPlan = row => { setEditingPlan(row); setPlan(Object.fromEntries(Object.keys(blankPlan).map(key => [key, key === 'account_cpu_cores' ? (row.account_cpu_cores ?? row.account_cpu_pct / 100) : row[key]]))); setPlanOpen(true) }
  return <div><PageHeader title="Reseller Management" description="Create reseller plans, issue reseller logins, and control how many hosting accounts and how much disk each reseller may allocate." icon={Store}><Button variant="secondary" onClick={openNewPlan}><Plus className="h-4 w-4" />New plan</Button><Button disabled={!planRows.length} onClick={() => setResellerOpen(true)}><Plus className="h-4 w-4" />New reseller</Button></PageHeader>
    <Tabs defaultValue="resellers"><TabsList><TabsTrigger value="resellers">Resellers</TabsTrigger><TabsTrigger value="plans">Plans</TabsTrigger></TabsList>
      <TabsContent value="resellers"><DataTable data={resellers.data?.resellers} loading={resellers.isLoading} error={resellers.error} onRetry={resellers.refetch} filterable emptyTitle="No resellers" emptyDescription="Create a reseller plan, then issue a reseller login." columns={[
        { key: 'username', header: 'Login', sortable: true, searchable: true, render: row => <span className="font-semibold">{row.username}</span> },
        { key: 'company', header: 'Company', render: row => row.company || '—' },
        { key: 'plan_name', header: 'Plan', sortable: true, render: row => <Select className="min-w-32" aria-label={`Plan for ${row.username}`} value={row.plan_id} disabled={updateReseller.isPending} onChange={event => updateReseller.mutate({ row, changes: { plan_id: Number(event.target.value) } })}>{planRows.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</Select> },
        { key: 'account_count', header: 'Accounts', render: row => `${row.account_count} / ${row.max_accounts}` },
        { key: 'status', header: 'Status', render: row => <StatusBadge status={row.status} /> },
        { key: 'actions', header: '', render: row => <Button size="sm" variant="secondary" loading={updateReseller.isPending && updateReseller.variables?.row.id === row.id} onClick={() => updateReseller.mutate({ row, changes: { status: row.status === 'active' ? 'suspended' : 'active' } })}>{row.status === 'active' ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}{row.status === 'active' ? 'Suspend' : 'Activate'}</Button> },
      ]} /></TabsContent>
      <TabsContent value="plans"><DataTable data={planRows} loading={plans.isLoading} error={plans.error} onRetry={plans.refetch} emptyTitle="No reseller plans" emptyDescription="Plans cap reseller account count and total allocated disk." columns={[
        { key: 'name', header: 'Plan', sortable: true, render: row => <span className="font-semibold">{row.name}</span> },
        { key: 'max_accounts', header: 'Accounts' }, { key: 'max_total_disk_mb', header: 'Total disk', render: row => `${Math.round(row.max_total_disk_mb / 1024)} GB` },
        { key: 'account_quota_hard_mb', header: 'Per account', render: row => `${Math.round(row.account_quota_hard_mb / 1024)} GB` }, { key: 'php_version', header: 'PHP', render: row => `PHP ${row.php_version}` }, { key: 'reseller_count', header: 'Assigned' },
        { key: 'actions', header: '', render: row => <div className="flex justify-end gap-2"><Button size="sm" variant="secondary" onClick={() => openEditPlan(row)}><Pencil className="h-4 w-4" />Edit</Button><Button size="sm" variant="danger" disabled={row.reseller_count > 0} title={row.reseller_count > 0 ? 'Reassign resellers before deleting this plan' : undefined} onClick={() => setDeletingPlan(row)}><Trash2 className="h-4 w-4" />Delete</Button></div> },
      ]} /></TabsContent>
    </Tabs>

    <Dialog open={planOpen} onOpenChange={value => { setPlanOpen(value); if (!value) setEditingPlan(null) }}><DialogContent size="lg"><DialogHeader><DialogTitle>{editingPlan ? 'Edit reseller plan' : 'New reseller plan'}</DialogTitle><DialogDescription>These defaults apply to every account the reseller creates. Existing accounts keep their current resource limits.</DialogDescription></DialogHeader><form onSubmit={event => { event.preventDefault(); savePlan.mutate() }}><DialogBody className="grid gap-4 sm:grid-cols-2"><FormField label="Plan name" htmlFor="reseller-plan-name"><Input id="reseller-plan-name" required value={plan.name} onChange={event => setPlan(value => ({ ...value, name: event.target.value }))} /></FormField>{[['max_accounts','Maximum accounts'],['max_total_disk_mb','Total disk (MB)'],['account_quota_soft_mb','Account soft quota (MB)'],['account_quota_hard_mb','Account hard quota (MB)'],['account_cpu_cores','Account CPU cores'],['account_mem_mb','Account memory (MB)'],['account_io_mb','Account I/O (MB/s)'],['account_pids_max','Account process limit']].map(([key, label]) => <FormField key={key} label={label} htmlFor={`reseller-plan-${key}`}><Input id={`reseller-plan-${key}`} type="number" step={key === 'account_cpu_cores' ? '0.25' : '1'} min={key === 'account_cpu_cores' ? 0.01 : key === 'account_mem_mb' ? 64 : key === 'account_pids_max' ? 10 : 1} required value={plan[key]} onChange={event => setPlan(value => ({ ...value, [key]: event.target.value }))} /></FormField>)}<FormField label="PHP version" htmlFor="reseller-plan-php"><Select id="reseller-plan-php" value={plan.php_version} onChange={event => setPlan(value => ({ ...value, php_version: event.target.value }))}>{['8.1','8.2','8.3','8.4','8.5'].map(version => <option key={version}>{version}</option>)}</Select></FormField></DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setPlanOpen(false)}>Cancel</Button><Button type="submit" loading={savePlan.isPending}>{editingPlan ? 'Save changes' : 'Create plan'}</Button></DialogFooter></form></DialogContent></Dialog>
    <Dialog open={resellerOpen} onOpenChange={setResellerOpen}><DialogContent size="md"><DialogHeader><DialogTitle>New reseller</DialogTitle><DialogDescription>Creates a separate reseller panel login on the administrator port.</DialogDescription></DialogHeader><form onSubmit={event => { event.preventDefault(); createReseller.mutate() }}><DialogBody className="space-y-4"><FormField label="Username" htmlFor="new-reseller-username"><Input id="new-reseller-username" required pattern="[a-z][a-z0-9]{0,15}" value={reseller.username} onChange={event => setReseller(value => ({ ...value, username: event.target.value }))} /></FormField><FormField label="Company" htmlFor="new-reseller-company"><Input id="new-reseller-company" value={reseller.company} onChange={event => setReseller(value => ({ ...value, company: event.target.value }))} /></FormField><FormField label="Plan" htmlFor="new-reseller-plan"><Select id="new-reseller-plan" required value={reseller.plan_id} onChange={event => setReseller(value => ({ ...value, plan_id: event.target.value }))}><option value="">Choose a plan</option>{planRows.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</Select></FormField><FormField label="Password" htmlFor="new-reseller-password" hint="Leave blank to generate a strong password."><Input id="new-reseller-password" type="password" value={reseller.password} onChange={event => setReseller(value => ({ ...value, password: event.target.value }))} /></FormField></DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setResellerOpen(false)}>Cancel</Button><Button type="submit" loading={createReseller.isPending}>Create reseller</Button></DialogFooter></form></DialogContent></Dialog>
    <Credentials result={credentials} onClose={() => setCredentials(null)} />
    <ConfirmDialog open={!!deletingPlan} onOpenChange={value => !value && setDeletingPlan(null)} title={`Delete ${deletingPlan?.name || 'plan'}?`} description="The plan will be removed permanently. Plans assigned to a reseller cannot be deleted." confirmLabel="Delete plan" variant="danger" loading={removePlan.isPending} onConfirm={() => removePlan.mutate(deletingPlan)} />
  </div>
}

function ResellerPanel() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [credentials, setCredentials] = useState(null)
  const [confirm, setConfirm] = useState(null)
  const [form, setForm] = useState({ username: '', primary_domain: '', password: '' })
  const dashboard = useQuery({ queryKey: ['reseller-dashboard'], queryFn: () => get('/api/v1/reseller/dashboard') })
  const refresh = () => qc.invalidateQueries({ queryKey: ['reseller-dashboard'] })
  const create = useMutation({ mutationFn: () => post('/api/v1/reseller/accounts', { ...form, primary_domain: form.primary_domain || undefined, password: form.password || undefined }), onSuccess: result => { setCredentials(result); setOpen(false); setForm({ username: '', primary_domain: '', password: '' }); refresh() }, onError: error => toast.error('Could not create account', error.message) })
  const lifecycle = useMutation({ mutationFn: ({ row, action }) => post(`/api/v1/reseller/accounts/${row.username}/${action}`, {}), onSuccess: () => { toast.success('Account updated'); setConfirm(null); refresh() }, onError: error => toast.error('Could not update account', error.message) })
  const data = dashboard.data
  const accountPct = data ? Math.round(data.usage.accounts / data.plan.max_accounts * 100) : 0
  const diskPct = data ? Math.round(data.usage.disk_mb / data.plan.max_total_disk_mb * 100) : 0
  return <div><PageHeader title="Reseller Dashboard" description={`Manage hosting accounts${data?.profile.company ? ` for ${data.profile.company}` : ''} within your assigned plan.`} icon={Store}><Button onClick={() => setOpen(true)} disabled={!data || data.usage.accounts >= data.plan.max_accounts}><Plus className="h-4 w-4" />Create account</Button></PageHeader>
    <div className="mb-6 grid gap-4 sm:grid-cols-3"><Card><CardContent><p className="text-xs text-muted-foreground">Plan</p><p className="mt-1 text-xl font-semibold">{data?.plan.name || '—'}</p></CardContent></Card><Card><CardContent><p className="text-xs text-muted-foreground">Accounts</p><p className="mt-1 text-xl font-semibold">{data ? `${data.usage.accounts} / ${data.plan.max_accounts}` : '—'}</p><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-accent" style={{ width: `${Math.min(accountPct, 100)}%` }} /></div></CardContent></Card><Card><CardContent><p className="text-xs text-muted-foreground">Allocated disk</p><p className="mt-1 text-xl font-semibold">{data ? `${Math.round(data.usage.disk_mb / 1024)} / ${Math.round(data.plan.max_total_disk_mb / 1024)} GB` : '—'}</p><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-accent" style={{ width: `${Math.min(diskPct, 100)}%` }} /></div></CardContent></Card></div>
    <DataTable data={data?.accounts} loading={dashboard.isLoading} error={dashboard.error} onRetry={dashboard.refetch} filterable emptyTitle="No hosting accounts" emptyDescription="Create the first account under your reseller plan." emptyIcon={Users} columns={[
      { key: 'username', header: 'Account', sortable: true, searchable: true, render: row => <span className="font-semibold">{row.username}</span> }, { key: 'primary_domain', header: 'Primary domain', render: row => row.primary_domain || '—' }, { key: 'php_version', header: 'PHP', render: row => `PHP ${row.php_version}` }, { key: 'quota_hard_mb', header: 'Disk allocation', render: row => `${Math.round(row.quota_hard_mb / 1024)} GB` }, { key: 'status', header: 'Status', render: row => <StatusBadge status={row.status} /> },
      { key: 'actions', header: '', render: row => <div className="flex gap-2">{row.status === 'active' ? <Button size="sm" variant="secondary" onClick={() => lifecycle.mutate({ row, action: 'suspend' })}><Pause className="h-4 w-4" />Suspend</Button> : <Button size="sm" variant="secondary" onClick={() => lifecycle.mutate({ row, action: 'unsuspend' })}><Play className="h-4 w-4" />Activate</Button>}<Button size="sm" variant="danger" onClick={() => setConfirm(row)}><Trash2 className="h-4 w-4" />Terminate</Button></div> },
    ]} />
    <Dialog open={open} onOpenChange={setOpen}><DialogContent size="md"><DialogHeader><DialogTitle>Create hosting account</DialogTitle><DialogDescription>The account receives your plan’s PHP, disk, CPU, memory, I/O and process limits.</DialogDescription></DialogHeader><form onSubmit={event => { event.preventDefault(); create.mutate() }}><DialogBody className="space-y-4"><FormField label="Username" htmlFor="reseller-account-username"><Input id="reseller-account-username" required pattern="[a-z][a-z0-9]{0,15}" value={form.username} onChange={event => setForm(value => ({ ...value, username: event.target.value }))} /></FormField><FormField label="Primary domain" htmlFor="reseller-account-domain"><Input id="reseller-account-domain" value={form.primary_domain} onChange={event => setForm(value => ({ ...value, primary_domain: event.target.value }))} placeholder="example.com" /></FormField><FormField label="Password" htmlFor="reseller-account-password" hint="Leave blank to generate a strong password."><Input id="reseller-account-password" type="password" value={form.password} onChange={event => setForm(value => ({ ...value, password: event.target.value }))} /></FormField></DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" loading={create.isPending}>Create account</Button></DialogFooter></form></DialogContent></Dialog>
    <Credentials result={credentials} onClose={() => setCredentials(null)} />
    <ConfirmDialog open={!!confirm} onOpenChange={value => !value && setConfirm(null)} title={`Terminate ${confirm?.username || 'account'}?`} description="This permanently removes the account’s websites, databases, mail, and system user." confirmationText={confirm?.username} confirmLabel="Terminate account" variant="danger" loading={lifecycle.isPending} onConfirm={() => lifecycle.mutate({ row: confirm, action: 'terminate' })} />
  </div>
}

export default function Resellers() {
  const role = useAuth(state => state.role)
  return role === 'admin' ? <AdminResellers /> : <ResellerPanel />
}
