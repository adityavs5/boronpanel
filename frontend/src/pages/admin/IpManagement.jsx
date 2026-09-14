import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Network, Plus, RefreshCw, Save, Trash2, UserRoundCog } from 'lucide-react'
import { del, get, post, put } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle, ConfirmDialog } from '@/components/ui/Dialog'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { ErrorState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'

function IpEditor({ entry, onSave, saving, onDelete }) {
  const [form, setForm] = useState({ allocation_mode: entry.allocation_mode, label: entry.label || '', active: entry.active })
  useEffect(() => setForm({ allocation_mode: entry.allocation_mode, label: entry.label || '', active: entry.active }), [entry])
  return <Card>
    <CardHeader className="flex-row items-start justify-between gap-3">
      <div className="min-w-0"><CardTitle className="break-all font-mono">{entry.address}</CardTitle><CardDescription>{entry.interface || 'Unknown interface'}{entry.prefix_length != null ? ` /${entry.prefix_length}` : ''} · {entry.family.toUpperCase()}</CardDescription></div>
      <div className="flex flex-wrap justify-end gap-2"><Badge variant={entry.present_on_host ? 'success' : 'danger'}>{entry.present_on_host ? 'Present' : 'Missing'}</Badge><Badge variant={entry.allocation_mode === 'dedicated' ? 'warning' : 'neutral'}>{entry.allocation_mode}</Badge></div>
    </CardHeader>
    <CardContent className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <FormField label="Use"><Select value={form.allocation_mode} onChange={(event) => setForm((value) => ({ ...value, allocation_mode: event.target.value }))}><option value="shared">Shared by accounts</option><option value="dedicated">One dedicated account</option></Select></FormField>
        <FormField label="Status"><Select value={form.active ? 'active' : 'disabled'} onChange={(event) => setForm((value) => ({ ...value, active: event.target.value === 'active' }))}><option value="active">Available</option><option value="disabled">Disabled</option></Select></FormField>
        <FormField label="Label"><Input value={form.label} placeholder="Primary shared IP" onChange={(event) => setForm((value) => ({ ...value, label: event.target.value }))} /></FormField>
      </div>
      <div className="rounded-btn border border-border bg-muted/30 p-3">
        <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Assigned accounts ({entry.assignment_count})</div>
        <div className="mt-2 flex flex-wrap gap-2">{entry.assignments?.length ? entry.assignments.map((item) => <Badge key={item.username} variant={item.status === 'active' ? 'success' : 'neutral'}>{item.username}</Badge>) : <span className="text-sm text-muted-foreground">No accounts</span>}</div>
      </div>
      <div className="flex flex-wrap justify-end gap-2"><Button variant="danger" size="sm" disabled={entry.assignment_count > 0} onClick={() => onDelete(entry)}><Trash2 className="h-4 w-4" /> Remove</Button><Button size="sm" loading={saving} onClick={() => onSave(entry.id, form)}><Save className="h-4 w-4" /> Save IP</Button></div>
    </CardContent>
  </Card>
}

export default function IpManagement() {
  const qc = useQueryClient()
  const [policy, setPolicy] = useState(null)
  const [assignOpen, setAssignOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importAddress, setImportAddress] = useState('')
  const [assignment, setAssignment] = useState({ username: '', selection: 'specific', server_ip_id: '' })
  const [removeEntry, setRemoveEntry] = useState(null)
  const state = useQuery({ queryKey: ['ip-management'], queryFn: () => get('/api/v1/admin/ip-management') })
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => get('/api/v1/accounts') })
  useEffect(() => { if (state.data?.policy) setPolicy(state.data.policy) }, [state.data?.policy])
  const invalidate = () => qc.invalidateQueries({ queryKey: ['ip-management'] })
  const activeIps = useMemo(() => (state.data?.ips || []).filter((entry) => entry.active && entry.present_on_host), [state.data?.ips])

  const importMut = useMutation({ mutationFn: () => post('/api/v1/admin/ip-management/import', { addresses: [importAddress] }), onSuccess: () => { toast.success('Server IP imported'); setImportOpen(false); setImportAddress(''); invalidate() }, onError: (error) => toast.error('Could not import address', error.message) })
  const saveIpMut = useMutation({ mutationFn: ({ id, form }) => put(`/api/v1/admin/ip-management/ips/${id}`, form), onSuccess: () => { toast.success('IP settings saved'); invalidate() }, onError: (error) => toast.error('Could not save IP', error.message) })
  const policyMut = useMutation({ mutationFn: () => put('/api/v1/admin/ip-management/policy', { allocation_policy: policy.allocation_policy, default_server_ip_id: policy.allocation_policy === 'specific' ? Number(policy.default_server_ip_id) : null }), onSuccess: (result) => { setPolicy(result); toast.success('New-account IP policy saved'); invalidate() }, onError: (error) => toast.error('Could not save policy', error.message) })
  const assignMut = useMutation({ mutationFn: () => put(`/api/v1/admin/ip-management/accounts/${assignment.username}`, { selection: assignment.selection, server_ip_id: assignment.selection === 'specific' ? Number(assignment.server_ip_id) : null }), onSuccess: (result) => { result.warnings?.length ? toast.warning('IP assigned with DNS warnings', result.warnings.join('; ')) : toast.success('Account IP assigned', result.address || 'Primary server address'); setAssignOpen(false); setAssignment({ username: '', selection: 'specific', server_ip_id: '' }); invalidate(); qc.invalidateQueries({ queryKey: ['accounts'] }) }, onError: (error) => toast.error('Could not assign IP', error.message) })
  const deleteMut = useMutation({ mutationFn: (id) => del(`/api/v1/admin/ip-management/ips/${id}`), onSuccess: () => { toast.success('IP removed from Boron'); setRemoveEntry(null); invalidate() }, onError: (error) => toast.error('Could not remove IP', error.message) })

  if (state.isLoading) return <div className="grid gap-4 lg:grid-cols-2"><CardSkeleton /><CardSkeleton /></div>
  if (state.isError) return <ErrorState error={state.error} onRetry={state.refetch} />
  return <div className="space-y-6">
    <PageHeader title="IP Management" description="Allocate host IPs to accounts as shared or dedicated addresses." icon={Network}>
      {!!state.data.detected?.length && <Button variant="outline" onClick={() => { setImportAddress(state.data.detected[0].address); setImportOpen(true) }}><RefreshCw className="h-4 w-4" /> Add detected IP</Button>}
      <Button onClick={() => setAssignOpen(true)} disabled={!activeIps.length}><UserRoundCog className="h-4 w-4" /> Assign account</Button>
    </PageHeader>

    <Card><CardHeader><CardTitle>New-account allocation</CardTitle><CardDescription>Choose how an address is selected when an administrator creates a hosting account. A per-account selection in the creation wizard overrides this policy.</CardDescription></CardHeader><CardContent className="grid items-end gap-4 md:grid-cols-[1fr_1fr_auto]">
      <FormField label="Policy"><Select value={policy?.allocation_policy || 'primary'} onChange={(event) => setPolicy((value) => ({ ...value, allocation_policy: event.target.value }))}><option value="primary">Primary server IP</option><option value="random_shared">Random active shared IP</option><option value="specific">Always use one shared IP</option></Select></FormField>
      <FormField label="Default IP"><Select disabled={policy?.allocation_policy !== 'specific'} value={policy?.default_server_ip_id || ''} onChange={(event) => setPolicy((value) => ({ ...value, default_server_ip_id: event.target.value }))}><option value="">Choose an IP…</option>{activeIps.filter((entry) => entry.allocation_mode === 'shared').map((entry) => <option key={entry.id} value={entry.id}>{entry.address}{entry.label ? ` — ${entry.label}` : ''}</option>)}</Select></FormField>
      <Button loading={policyMut.isPending} disabled={!policy || (policy.allocation_policy === 'specific' && !policy.default_server_ip_id)} onClick={() => policyMut.mutate()}><Save className="h-4 w-4" /> Save policy</Button>
    </CardContent></Card>

    {!state.data.ips.length ? <Card><CardContent className="py-12 text-center"><Network className="mx-auto h-10 w-10 text-muted-foreground" /><h2 className="mt-3 font-semibold">No IPs imported</h2><p className="mt-1 text-sm text-muted-foreground">Attach addresses through your provider or operating-system network configuration, then import a detected address here.</p>{state.data.detected?.length > 0 && <Button className="mt-4" onClick={() => { setImportAddress(state.data.detected[0].address); setImportOpen(true) }}><Plus className="h-4 w-4" /> Add detected IP</Button>}</CardContent></Card> : <div className="grid gap-4 xl:grid-cols-2">{state.data.ips.map((entry) => <IpEditor key={entry.id} entry={entry} saving={saveIpMut.isPending && saveIpMut.variables?.id === entry.id} onSave={(id, form) => saveIpMut.mutate({ id, form })} onDelete={setRemoveEntry} />)}</div>}

    <Dialog open={importOpen} onOpenChange={setImportOpen}><DialogContent size="sm"><DialogHeader><DialogTitle>Add a detected server IP</DialogTitle></DialogHeader><form onSubmit={(event) => { event.preventDefault(); importMut.mutate() }}><DialogBody><FormField label="Host address" required hint="Only addresses currently bound to this server are listed."><Select required value={importAddress} onChange={(event) => setImportAddress(event.target.value)}>{state.data.detected.map((item) => <option key={item.address} value={item.address}>{item.address} — {item.interface}{item.prefix_length != null ? ` /${item.prefix_length}` : ''}</option>)}</Select></FormField></DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setImportOpen(false)}>Cancel</Button><Button type="submit" loading={importMut.isPending} disabled={!importAddress}>Add IP</Button></DialogFooter></form></DialogContent></Dialog>

    <Dialog open={assignOpen} onOpenChange={setAssignOpen}><DialogContent size="sm"><DialogHeader><DialogTitle>Assign an account IP</DialogTitle></DialogHeader><form onSubmit={(event) => { event.preventDefault(); assignMut.mutate() }}><DialogBody className="space-y-4">
      <FormField label="Account" required><Select required value={assignment.username} onChange={(event) => setAssignment((value) => ({ ...value, username: event.target.value }))}><option value="">Choose an account…</option>{(accounts.data || []).map((account) => <option key={account.username} value={account.username}>{account.username} — {account.server_ip || 'no IP'}</option>)}</Select></FormField>
      <FormField label="Assignment"><Select value={assignment.selection} onChange={(event) => setAssignment((value) => ({ ...value, selection: event.target.value }))}><option value="specific">Choose a specific IP</option><option value="random">Random shared IP</option><option value="primary">Primary server IP</option><option value="unassigned">Clear explicit assignment</option></Select></FormField>
      {assignment.selection === 'specific' && <FormField label="Server IP" required><Select required value={assignment.server_ip_id} onChange={(event) => setAssignment((value) => ({ ...value, server_ip_id: event.target.value }))}><option value="">Choose an IP…</option>{activeIps.map((entry) => <option key={entry.id} value={entry.id}>{entry.address} — {entry.allocation_mode}{entry.label ? ` · ${entry.label}` : ''}</option>)}</Select></FormField>}
      <p className="text-xs text-muted-foreground">Boron also updates this account’s records in zones managed by the panel. Existing external DNS remains untouched.</p>
    </DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setAssignOpen(false)}>Cancel</Button><Button type="submit" loading={assignMut.isPending} disabled={!assignment.username || (assignment.selection === 'specific' && !assignment.server_ip_id)}>Assign IP</Button></DialogFooter></form></DialogContent></Dialog>
    <ConfirmDialog open={!!removeEntry} onOpenChange={(open) => !open && setRemoveEntry(null)} title="Remove IP from Boron?" description={removeEntry ? `${removeEntry.address} will be removed from the allocation inventory. The address remains configured on the server.` : ''} confirmLabel="Remove IP" loading={deleteMut.isPending} onConfirm={() => deleteMut.mutate(removeEntry.id)} />
  </div>
}
