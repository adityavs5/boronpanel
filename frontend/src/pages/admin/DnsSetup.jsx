import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, Network, RefreshCw, Route, Server, Stethoscope } from 'lucide-react'
import { get, post, put } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { DataTable } from '@/components/ui/Table'
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/Dialog'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { toast } from '@/components/ui/Toast'

const MODES = [
  { id: 'cloudflare', title: 'Cloudflare DNS', icon: Cloud, description: 'Create new zones in Cloudflare and show the assigned registrar nameservers.' },
  { id: 'local', title: 'Local authoritative DNS', icon: Server, description: 'Serve zones from this server with PowerDNS over TCP and UDP port 53.' },
  { id: 'cluster', title: 'DNS cluster', icon: Network, description: 'Serve locally and replicate allowed zones to configured authoritative peers.' },
]

function splitNameservers(value) {
  return value.split(/[\s,]+/).map(item => item.trim()).filter(Boolean)
}

export default function DnsSetup() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['dns-setup'], queryFn: () => get('/api/v1/admin/dns-setup') })
  const [selected, setSelected] = useState(null)
  const [nameservers, setNameservers] = useState('')
  const [preview, setPreview] = useState(null)
  const [migration, setMigration] = useState(null)
  const [zoneForDiagnostics, setZoneForDiagnostics] = useState('')
  const [diagnostics, setDiagnostics] = useState(null)
  const mode = selected || query.data?.mode || 'local'
  const effectiveNameservers = nameservers || (query.data?.local_nameservers || []).join('\n')
  const payload = useMemo(() => ({ mode, local_nameservers: mode === 'cloudflare' ? [] : splitNameservers(effectiveNameservers) }), [mode, effectiveNameservers])
  const invalidate = () => qc.invalidateQueries({ queryKey: ['dns-setup'] })

  const previewMode = useMutation({
    mutationFn: () => post('/api/v1/admin/dns-setup/mode/preview', payload),
    onSuccess: setPreview,
    onError: error => toast.error('Could not preview DNS mode', error.message),
  })
  const saveMode = useMutation({
    mutationFn: () => put('/api/v1/admin/dns-setup/mode', payload),
    onSuccess: data => { setPreview(null); setSelected(data.mode); invalidate(); toast.success('DNS mode saved', 'Existing zones were left on their current providers.') },
    onError: error => toast.error('Could not save DNS mode', error.message),
  })
  const previewMigration = useMutation({
    mutationFn: ({ zone, target }) => post(`/api/v1/admin/dns-setup/zones/${encodeURIComponent(zone)}/migration/preview`, { target }),
    onSuccess: setMigration,
    onError: error => toast.error('Could not preview zone migration', error.message),
  })
  const applyMigration = useMutation({
    mutationFn: data => post(`/api/v1/admin/dns-setup/zones/${encodeURIComponent(data.zone)}/migration`, { target: data.target, confirm: true }),
    onSuccess: data => { setMigration(null); invalidate(); toast.success('Zone migration staged', data.state === 'waiting_for_delegation' ? 'Update registrar nameservers, then verify delegation before relying on Cloudflare.' : 'The destination is staged; verify authority before changing delegation.') },
    onError: error => toast.error('Could not stage zone migration', error.message),
  })
  const runDiagnostics = useMutation({
    mutationFn: () => post('/api/v1/admin/dns-setup/diagnostics', { zone: zoneForDiagnostics || null }),
    onSuccess: data => { setDiagnostics(data); invalidate() },
    onError: error => toast.error('DNS diagnostics failed', error.message),
  })
  const verifyMigration = useMutation({
    mutationFn: zone => post(`/api/v1/admin/dns-setup/zones/${encodeURIComponent(zone)}/migration/verify`),
    onSuccess: data => { invalidate(); data.ready ? toast.success('DNS migration completed') : toast.info('Migration is still waiting', data.state.replaceAll('_', ' ')) },
    onError: error => toast.error('Could not verify DNS migration', error.message),
  })

  return <div>
    <PageHeader title="DNS Setup" description="Choose the default DNS authority, migrate zones deliberately, and verify public delegation." icon={Route}>
      <Button variant="secondary" asChild><Link to="/cloudflare"><Cloud className="h-4 w-4" />Cloudflare accounts</Link></Button>
      <Button variant="secondary" asChild><Link to="/dns-cluster"><Network className="h-4 w-4" />Cluster peers</Link></Button>
    </PageHeader>

    <Card className="mb-5"><CardHeader><div><CardTitle>Operating mode for new zones</CardTitle><CardDescription>Changing this default never moves an existing zone. Each existing zone has a separate staged migration below.</CardDescription></div></CardHeader><CardContent className="space-y-5">
      <div className="grid gap-3 lg:grid-cols-3">{MODES.map(item => { const Icon = item.icon; const active = mode === item.id; return <button key={item.id} type="button" onClick={() => { setSelected(item.id); setPreview(null) }} className={`rounded-card border p-4 text-left transition-colors ${active ? 'border-accent bg-accent/5 ring-2 ring-accent/15' : 'border-border bg-card hover:border-accent/50'}`}><div className="mb-2 flex items-center gap-2"><Icon className="h-5 w-5 text-accent" /><span className="font-semibold">{item.title}</span>{query.data?.mode === item.id && <StatusBadge status="active" />}</div><p className="text-sm text-muted-foreground">{item.description}</p></button> })}</div>
      {mode !== 'cloudflare' && <FormField label="Authoritative nameservers" required hint="Enter two to eight hostnames, one per line. Child nameservers receive glue records automatically; external nameservers do not."><textarea className="min-h-24 w-full rounded-btn border border-input bg-input-surface px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:border-accent focus-visible:ring-[3px] focus-visible:ring-ring/25" value={effectiveNameservers} onChange={event => setNameservers(event.target.value)} placeholder={'ns1.example.net\nns2.example.net'} /></FormField>}
      {preview && <div className={`rounded-btn border p-4 text-sm ${preview.blockers?.length ? 'border-danger/40 bg-danger/5' : 'border-success/40 bg-success/5'}`}><p className="font-semibold">{preview.current_mode} → {preview.requested_mode}</p><p className="mt-1 text-muted-foreground">{preview.message}</p>{preview.blockers?.map(item => <p className="mt-2 text-danger" key={item}>{item}</p>)}</div>}
      <div className="flex flex-wrap justify-end gap-2"><Button variant="secondary" onClick={() => previewMode.mutate()} loading={previewMode.isPending}>Preview change</Button><Button onClick={() => saveMode.mutate()} loading={saveMode.isPending} disabled={!preview || preview.requested_mode !== mode || preview.blockers?.length}>Save operating mode</Button></div>
    </CardContent></Card>

    <Card className="mb-5"><CardHeader><div><CardTitle>Managed zones</CardTitle><CardDescription>Provider changes copy and compare records first. Registrar delegation and DS records remain external actions until a registrar integration is configured.</CardDescription></div></CardHeader><CardContent>
      <DataTable data={query.data?.zones || []} loading={query.isLoading} error={query.error} onRetry={query.refetch} filterable searchPlaceholder="Search zones…" columns={[
        { key: 'zone', header: 'Zone', sortable: true, render: row => <span className="font-semibold">{row.zone}</span> },
        { key: 'provider', header: 'Effective provider', render: row => <div><StatusBadge status={row.cloudflare_status === 'pending' ? 'pending' : row.provider === 'cloudflare' ? 'active' : 'healthy'} label={row.cloudflare_status === 'pending' ? 'Cloudflare pending' : row.provider === 'cloudflare' ? 'Cloudflare' : row.provider === 'cluster' ? 'DNS cluster' : 'Local PowerDNS'} />{row.migration && row.migration.state !== 'completed' && <p className="mt-1 text-xs text-muted-foreground">Migration: {row.migration.state.replaceAll('_', ' ')}</p>}</div> },
        { key: 'nameservers', header: 'Nameservers', render: row => <span className="text-xs text-muted-foreground">{(row.nameservers || []).join(', ') || 'Not reported'}</span> },
        { key: 'actions', header: '', searchable: false, render: row => <div className="flex justify-end gap-2">{row.migration && row.migration.state !== 'completed' && <Button size="sm" onClick={() => verifyMigration.mutate(row.zone)} loading={verifyMigration.isPending && verifyMigration.variables === row.zone}>Verify migration</Button>}{MODES.filter(item => item.id !== row.provider && (!row.migration || row.migration.state === 'completed')).map(item => <Button key={item.id} size="sm" variant="secondary" onClick={() => previewMigration.mutate({ zone: row.zone, target: item.id })} loading={previewMigration.isPending && previewMigration.variables?.zone === row.zone}>{item.id === 'cluster' ? 'Stage for cluster' : `Move to ${item.id}`}</Button>)}</div> },
      ]} emptyTitle="No managed zones" emptyDescription="Zones appear here after a hosting domain is created." />
    </CardContent></Card>

    <Card><CardHeader><div><CardTitle className="flex items-center gap-2"><Stethoscope className="h-4 w-4" />Authority diagnostics</CardTitle><CardDescription>Checks PowerDNS service/listeners and, for an optional zone, the nameservers and SOA visible through DNS.</CardDescription></div></CardHeader><CardContent className="space-y-4"><div className="flex flex-col gap-2 sm:flex-row"><Input value={zoneForDiagnostics} onChange={event => setZoneForDiagnostics(event.target.value)} placeholder="Optional zone, e.g. example.com" /><Button onClick={() => runDiagnostics.mutate()} loading={runDiagnostics.isPending}><RefreshCw className="h-4 w-4" />Run diagnostics</Button></div>{diagnostics && <div className="grid gap-3 rounded-btn border border-border bg-muted/30 p-4 text-sm sm:grid-cols-3"><div><span className="block text-muted-foreground">PowerDNS</span><strong>{diagnostics.powerdns_active ? 'Active' : 'Inactive'}</strong></div><div><span className="block text-muted-foreground">TCP/UDP port 53</span><strong>{diagnostics.authoritative_tcp_udp_53 ? 'Listening' : 'Not detected'}</strong></div><div><span className="block text-muted-foreground">Public delegation</span><strong>{diagnostics.delegation ? diagnostics.delegation.resolved ? 'Resolved' : 'Not resolved' : 'Zone not selected'}</strong></div>{diagnostics.delegation && <div className="sm:col-span-3"><span className="block text-muted-foreground">Observed nameservers</span><span>{diagnostics.delegation.nameservers?.join(', ') || 'None'}</span></div>}</div>}</CardContent></Card>

    <Dialog open={!!migration} onOpenChange={open => { if (!open) setMigration(null) }}><DialogContent size="lg"><DialogHeader><DialogTitle>Stage DNS migration for {migration?.zone}</DialogTitle><DialogDescription>Records are prepared before authority changes. Review differences and external actions before continuing.</DialogDescription></DialogHeader><DialogBody className="space-y-4">{migration && <><div className="grid gap-3 sm:grid-cols-3"><div className="rounded-btn border border-border p-3"><span className="text-xs text-muted-foreground">Current</span><p className="font-semibold">{migration.source}</p></div><div className="rounded-btn border border-border p-3"><span className="text-xs text-muted-foreground">Target</span><p className="font-semibold">{migration.target}</p></div><div className="rounded-btn border border-border p-3"><span className="text-xs text-muted-foreground">Records</span><p className="font-semibold">{migration.record_count}</p></div></div><div className="grid gap-3 sm:grid-cols-3">{['create', 'update', 'remove'].map(kind => <div key={kind}><p className="mb-1 text-sm font-semibold capitalize">{kind} ({migration.diff?.[kind]?.length || 0})</p><div className="max-h-36 overflow-auto rounded-btn bg-muted/40 p-2 text-xs">{migration.diff?.[kind]?.length ? migration.diff[kind].map(item => <div key={item}>{item}</div>) : 'None'}</div></div>)}</div><div className="rounded-btn border border-warning/40 bg-warning/5 p-3 text-sm">{migration.warnings?.map(item => <p key={item}>• {item}</p>)}</div></>}</DialogBody><DialogFooter><Button variant="secondary" onClick={() => setMigration(null)}>Cancel</Button><Button onClick={() => applyMigration.mutate(migration)} loading={applyMigration.isPending}>Stage migration</Button></DialogFooter></DialogContent></Dialog>
  </div>
}
