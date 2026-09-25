import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Network, Plus, RefreshCw, Trash2, Zap } from 'lucide-react'
import { del, get, patch, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, ConfirmDialog } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

const EMPTY = { name: '', peer_type: 'directadmin', endpoint: '', username: 'admin', credential: '', verify_tls: true, direction: 'push', zones_text: '' }
const selectClass = 'flex h-9 w-full rounded-btn border border-input bg-input-surface px-3 text-sm text-foreground shadow-sm focus-visible:outline-none focus-visible:border-accent focus-visible:ring-[3px] focus-visible:ring-ring/25'

export default function DnsCluster() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [removePeer, setRemovePeer] = useState(null)
  const [generated, setGenerated] = useState(null)
  const [form, setForm] = useState(EMPTY)
  const query = useQuery({ queryKey: ['dns-cluster'], queryFn: () => get('/api/v1/admin/dns-cluster'), refetchInterval: 10000 })
  const refresh = () => qc.invalidateQueries({ queryKey: ['dns-cluster'] })
  const create = useMutation({
    mutationFn: () => post('/api/v1/admin/dns-cluster/peers', {
      ...form, credential: form.credential || null,
      zones: form.zones_text.split(/[\s,]+/).map(value => value.trim()).filter(Boolean),
      zones_text: undefined,
    }),
    onSuccess: result => {
      setOpen(false); setForm(EMPTY); refresh()
      if (result.generated_credential) setGenerated(result.generated_credential)
      toast.success('DNS peer added')
    },
    onError: error => toast.error('Could not add DNS peer', error.message),
  })
  const test = useMutation({
    mutationFn: id => post(`/api/v1/admin/dns-cluster/peers/${id}/test`),
    onSuccess: () => { toast.success('Peer connection verified'); refresh() },
    onError: error => { toast.error('Peer test failed', error.message); refresh() },
  })
  const toggle = useMutation({
    mutationFn: peer => patch(`/api/v1/admin/dns-cluster/peers/${peer.id}`, { enabled: !peer.enabled }),
    onSuccess: () => { toast.success('Peer updated'); refresh() },
    onError: error => toast.error('Could not update peer', error.message),
  })
  const remove = useMutation({
    mutationFn: id => del(`/api/v1/admin/dns-cluster/peers/${id}`),
    onSuccess: () => { setRemovePeer(null); refresh(); toast.success('DNS peer removed') },
    onError: error => toast.error('Could not remove peer', error.message),
  })
  const sync = useMutation({
    mutationFn: () => post('/api/v1/admin/dns-cluster/sync-all'),
    onSuccess: result => { refresh(); toast.success('Full DNS sync queued', `${result.zones} zones · ${result.queued} deliveries`) },
    onError: error => toast.error('Could not queue DNS sync', error.message),
  })
  const peers = query.data?.peers || []
  const jobs = query.data?.jobs || []
  const typeHelp = form.peer_type === 'directadmin'
    ? 'Use a restricted DirectAdmin login key with CMD_API_DNS_ADMIN and CMD_API_LOGIN_TEST access.'
    : form.peer_type === 'cpanel'
      ? 'Use a restricted WHM API token with DNS zone management permissions.'
      : 'Leave the key empty to generate a shared Boron cluster key, then configure that same key on the other node.'

  return <div>
    <PageHeader title="DNS Cluster" description="Replicate complete PowerDNS zones to Boron, DirectAdmin, or cPanel/WHM while keeping your current authoritative nameservers." icon={Network}>
      <Button variant="secondary" onClick={() => sync.mutate()} loading={sync.isPending} disabled={!peers.length}><RefreshCw className="h-4 w-4" />Sync all zones</Button>
      <Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" />Add peer</Button>
    </PageHeader>

    <Card className="mb-5"><CardHeader><div><CardTitle>Authoritative peers</CardTitle><CardDescription>Credentials are encrypted at rest and never displayed again. Failed deliveries retry automatically with backoff.</CardDescription></div></CardHeader><CardContent>
      <DataTable data={peers} loading={query.isLoading} error={query.error} onRetry={query.refetch} filterable searchPlaceholder="Search DNS peers…" columns={[
        { key: 'name', header: 'Peer', sortable: true, render: row => <div><div className="font-semibold">{row.name}</div><div className="text-xs text-muted-foreground">{row.endpoint}</div></div> },
        { key: 'peer_type', header: 'Type', sortable: true, render: row => ({ directadmin: 'DirectAdmin', cpanel: 'cPanel / WHM', boron: 'Boron' }[row.peer_type] || row.peer_type) },
        { key: 'direction', header: 'Direction', render: row => <div><span className="capitalize">{row.direction}</span><div className="text-xs text-muted-foreground">{row.zones?.length ? `${row.zones.length} owned zone(s)` : 'All local zones'}</div></div> },
        { key: 'status', header: 'Health', render: row => <StatusBadge status={!row.enabled ? 'disabled' : row.status} /> },
        { key: 'last_success_at', header: 'Last success', sortable: true, render: row => row.last_success_at ? new Date(row.last_success_at).toLocaleString() : 'Not synced yet' },
        { key: 'actions', header: '', searchable: false, render: row => <div className="flex justify-end gap-2"><Button size="sm" variant="secondary" onClick={() => test.mutate(row.id)} loading={test.isPending && test.variables === row.id}><Zap className="h-3.5 w-3.5" />Test</Button><Button size="sm" variant="secondary" onClick={() => toggle.mutate(row)} loading={toggle.isPending && toggle.variables?.id === row.id}>{row.enabled ? 'Disable' : 'Enable'}</Button><Button size="icon-sm" variant="ghost" title="Remove peer" onClick={() => setRemovePeer(row)}><Trash2 className="h-4 w-4" /></Button></div> },
      ]} emptyTitle="No DNS peers" emptyDescription="Add a peer to keep its authoritative copy of every local zone synchronized." emptyIcon={Network} />
    </CardContent></Card>

    <Card><CardHeader><div><CardTitle>Delivery queue</CardTitle><CardDescription>Only pending and failed work remains here; completed deliveries are removed automatically.</CardDescription></div></CardHeader><CardContent>
      <DataTable data={jobs} columns={[
        { key: 'zone', header: 'Zone', searchable: true, render: row => <span className="font-medium">{row.zone}</span> },
        { key: 'action', header: 'Action' },
        { key: 'status', header: 'Status', render: row => <StatusBadge status={row.status} /> },
        { key: 'attempts', header: 'Attempts', align: 'right' },
        { key: 'last_error', header: 'Last result', render: row => <span className="block max-w-md truncate text-muted-foreground" title={row.last_error || ''}>{row.last_error || 'Queued'}</span> },
      ]} emptyTitle="Queue is clear" emptyDescription="All queued DNS changes have been delivered." />
    </CardContent></Card>

    <Dialog open={open} onOpenChange={setOpen}><DialogContent size="md"><DialogHeader><DialogTitle>Add DNS cluster peer</DialogTitle><DialogDescription>Existing registrar nameservers do not change. Boron sends each current full zone to the selected authoritative server.</DialogDescription></DialogHeader><form onSubmit={event => { event.preventDefault(); create.mutate() }}><DialogBody className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2"><FormField label="Display name" required><Input required value={form.name} onChange={event => setForm(value => ({ ...value, name: event.target.value }))} placeholder="Secondary DNS" /></FormField><FormField label="Server type" required><select className={selectClass} value={form.peer_type} onChange={event => { const type = event.target.value; setForm(value => ({ ...value, peer_type: type, direction: type === 'boron' ? value.direction : 'push', username: type === 'boron' ? '' : type === 'cpanel' ? 'root' : 'admin' })) }}><option value="directadmin">DirectAdmin</option><option value="cpanel">cPanel / WHM</option><option value="boron">Boron Panel</option></select></FormField></div>
      <FormField label="Server URL" required hint={form.peer_type === 'directadmin' ? 'Example: https://dns2.example.com:2222' : form.peer_type === 'cpanel' ? 'Example: https://dns2.example.com:2087' : 'Example: https://panel2.example.com'}><Input required type="url" value={form.endpoint} onChange={event => setForm(value => ({ ...value, endpoint: event.target.value }))} /></FormField>
      {form.peer_type !== 'boron' && <FormField label={form.peer_type === 'cpanel' ? 'WHM username' : 'DirectAdmin username'} required><Input required autoComplete="off" value={form.username} onChange={event => setForm(value => ({ ...value, username: event.target.value }))} /></FormField>}
      <FormField label={form.peer_type === 'boron' ? 'Shared cluster key' : form.peer_type === 'cpanel' ? 'WHM API token' : 'DirectAdmin login key'} required={form.peer_type !== 'boron'} hint={typeHelp}><Input type="password" autoComplete="new-password" required={form.peer_type !== 'boron'} value={form.credential} onChange={event => setForm(value => ({ ...value, credential: event.target.value }))} /></FormField>
      {form.peer_type === 'boron' && <FormField label="Replication direction" required hint="Receiving is allowed only for the explicit zone ownership list below."><select className={selectClass} value={form.direction} onChange={event => setForm(value => ({ ...value, direction: event.target.value }))}><option value="push">Push to peer</option><option value="receive">Receive from peer</option><option value="bidirectional">Bidirectional</option></select></FormField>}
      <FormField label="Zone ownership" required={form.direction !== 'push'} hint={form.direction === 'push' ? 'Optional. Leave empty to push every local zone, or enter specific zones separated by commas or lines.' : 'Required for receive/bidirectional. Only these exact zones can be changed by this peer.'}><textarea className="min-h-20 w-full rounded-btn border border-input bg-input-surface px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:border-accent focus-visible:ring-[3px] focus-visible:ring-ring/25" required={form.direction !== 'push'} value={form.zones_text} onChange={event => setForm(value => ({ ...value, zones_text: event.target.value }))} placeholder={'example.com\nexample.net'} /></FormField>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 h-4 w-4 accent-accent" checked={form.verify_tls} onChange={event => setForm(value => ({ ...value, verify_tls: event.target.checked }))} /><span><span className="block font-medium">Verify TLS certificate</span><span className="text-xs text-muted-foreground">Keep enabled for production. Disable only for a temporary peer with a self-signed certificate.</span></span></label>
    </DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" loading={create.isPending}>Add peer</Button></DialogFooter></form></DialogContent></Dialog>

    <Dialog open={!!generated} onOpenChange={() => {}}><DialogContent size="sm" showClose={false}><DialogHeader><DialogTitle>Save the Boron cluster key</DialogTitle><DialogDescription>This key is shown once. Add the same key when configuring the corresponding Boron peer on the other server.</DialogDescription></DialogHeader><DialogBody><div className="flex gap-2"><Input readOnly className="font-mono" value={generated || ''} /><Button size="icon" variant="secondary" title="Copy key" onClick={async () => { await navigator.clipboard.writeText(generated || ''); toast.success('Cluster key copied') }}><Copy className="h-4 w-4" /></Button></div></DialogBody><DialogFooter><Button onClick={() => setGenerated(null)}>I saved it</Button></DialogFooter></DialogContent></Dialog>

    <ConfirmDialog open={!!removePeer} onOpenChange={openValue => { if (!openValue) setRemovePeer(null) }} title="Remove DNS peer?" description="Queued deliveries for this peer will also be removed. Zones already copied to the remote server are left in place." confirmLabel="Remove peer" loading={remove.isPending} onConfirm={() => remove.mutate(removePeer.id)} />
  </div>
}
