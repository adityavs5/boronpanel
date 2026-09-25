import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Flame, Plus, Trash2, ShieldCheck, ShieldOff, Network, RotateCcw, TimerReset, Download, Upload, Ban, History } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

const EMPTY_FORM = { action: 'allow', port: '', protocol: 'any', direction: 'in', from_addr: '', to_addr: '', comment: '' }

export default function Firewall() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [toggleAction, setToggleAction] = useState(null) // 'enable' | 'disable' | null
  const [deleteRule, setDeleteRule] = useState(null)
  const [bypassOpen, setBypassOpen] = useState(false)
  const [bypassForm, setBypassForm] = useState({ address: '', label: '' })
  const [deleteBypass, setDeleteBypass] = useState(null)
  const [confirmationToken, setConfirmationToken] = useState('')
  const [now, setNow] = useState(() => Date.now())
  const [preset, setPreset] = useState({ preset_id: '', action: 'allow', address: '' })
  const [banOpen, setBanOpen] = useState(false)
  const [banForm, setBanForm] = useState({ value: '', duration_minutes: 60, reason: '' })
  const [deleteBan, setDeleteBan] = useState(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [replaceImport, setReplaceImport] = useState(false)
  const [importPreview, setImportPreview] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['firewall-rules', username],
    queryFn: () => get('/api/v1/firewall/rules'),
  })

  const active = data?.active
  const pending = data?.pending_change
  const secondsLeft = pending ? Math.max(0, Math.ceil(pending.expires_at - now / 1000)) : 0
  const invalidate = () => qc.invalidateQueries({ queryKey: ['firewall-rules', username] })

  useEffect(() => {
    if (!pending) return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [pending?.change_id])

  const changeApplied = (response, message) => {
    setConfirmationToken(response?.pending_change?.confirmation_token || '')
    setNow(Date.now())
    toast.warning('Confirm firewall access', `${message}. Keep this change within two minutes or Boron will revert it.`)
    invalidate()
  }

  const toggleMut = useMutation({
    mutationFn: (action) => post(`/api/v1/firewall/${action}`, { confirm: true }),
    onSuccess: (res, action) => {
      changeApplied(res, action === 'enable' ? 'Firewall enabled temporarily' : 'Firewall disabled temporarily')
      setToggleAction(null)
    },
    onError: (e) => toast.error('Action failed', e.message),
  })

  const addMut = useMutation({
    mutationFn: (body) => post('/api/v1/firewall/rules', body),
    onSuccess: (res) => {
      changeApplied(res, 'Rule added temporarily')
      setAddOpen(false)
      setForm(EMPTY_FORM)
    },
    onError: (e) => toast.error('Could not add rule', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (ruleId) => del(`/api/v1/firewall/rules/${ruleId}`),
    onSuccess: (res) => {
      changeApplied(res, 'Rule deleted temporarily')
      setDeleteRule(null)
    },
    onError: (e) => toast.error('Could not delete rule', e.message),
  })

  const addBypassMut = useMutation({
    mutationFn: (body) => post('/api/v1/firewall/bypass', body),
    onSuccess: (res) => {
      changeApplied(res, 'Full-access IP added temporarily')
      setBypassOpen(false)
      setBypassForm({ address: '', label: '' })
    },
    onError: (e) => toast.error('Could not add bypass IP', e.message),
  })

  const deleteBypassMut = useMutation({
    mutationFn: (id) => del(`/api/v1/firewall/bypass/${id}`),
    onSuccess: (res) => {
      changeApplied(res, 'Full-access IP removed temporarily')
      setDeleteBypass(null)
    },
    onError: (e) => toast.error('Could not remove bypass IP', e.message),
  })

  const presetMut = useMutation({
    mutationFn: () => post('/api/v1/firewall/presets', { ...preset, address: preset.address.trim() || 'any' }),
    onSuccess: (res) => changeApplied(res, 'Service preset applied temporarily'),
    onError: (e) => toast.error('Could not apply preset', e.message),
  })

  const banMut = useMutation({
    mutationFn: () => post('/api/v1/firewall/temporary-bans', { ...banForm, duration_minutes: Number(banForm.duration_minutes) }),
    onSuccess: (res) => { changeApplied(res, 'Temporary block applied'); setBanOpen(false); setBanForm({ value: '', duration_minutes: 60, reason: '' }) },
    onError: (e) => toast.error('Could not block address', e.message),
  })

  const deleteBanMut = useMutation({
    mutationFn: (id) => del(`/api/v1/firewall/temporary-bans/${id}`),
    onSuccess: (res) => { changeApplied(res, 'Temporary block removed'); setDeleteBan(null) },
    onError: (e) => toast.error('Could not remove block', e.message),
  })

  const parseImport = () => {
    try { return JSON.parse(importText) } catch { throw new Error('Choose a valid Boron firewall JSON file.') }
  }
  const previewImportMut = useMutation({
    mutationFn: () => post('/api/v1/firewall/configuration/preview', { configuration: parseImport(), replace: replaceImport }),
    onSuccess: setImportPreview,
    onError: (e) => toast.error('Could not preview import', e.message),
  })
  const importMut = useMutation({
    mutationFn: () => post('/api/v1/firewall/configuration/import', { configuration: parseImport(), replace: replaceImport }),
    onSuccess: (res) => { changeApplied(res, 'Firewall configuration imported temporarily'); setImportOpen(false); setImportPreview(null) },
    onError: (e) => toast.error('Could not import configuration', e.message),
  })

  const confirmMut = useMutation({
    mutationFn: () => post('/api/v1/firewall/pending/confirm', { confirmation_token: confirmationToken }),
    onSuccess: () => {
      toast.success('Firewall change kept')
      setConfirmationToken('')
      invalidate()
    },
    onError: (e) => toast.error('Could not confirm change', e.message),
  })

  const revertMut = useMutation({
    mutationFn: () => post('/api/v1/firewall/pending/revert', {}),
    onSuccess: () => {
      toast.success('Firewall change reverted')
      setConfirmationToken('')
      invalidate()
    },
    onError: (e) => toast.error('Could not revert change', e.message),
  })

  const columns = [
    {
      key: 'action',
      header: 'Action',
      sortable: true,
      searchable: true,
      render: (r) => <Badge variant={r.action === 'allow' ? 'success' : 'danger'}>{r.action}</Badge>,
    },
    {
      key: 'direction',
      header: 'Direction',
      sortable: true,
      searchable: true,
      render: (r) => r.direction === 'out' ? 'Outbound' : 'Inbound',
    },
    {
      key: 'port',
      header: 'Port',
      sortable: true,
      searchable: true,
      render: (r) => <span className="tabular-nums">{r.port}</span>,
    },
    { key: 'protocol', header: 'Protocol', sortable: true, render: (r) => r.protocol },
    { key: 'endpoint', header: 'Address', searchable: true, render: (r) => r.direction === 'out' ? `to ${r.to || 'any'}` : `from ${r.from || 'any'}` },
    {
      key: 'comment',
      header: 'Comment',
      searchable: true,
      render: (r) => r.comment || <span className="text-muted-foreground">—</span>,
    },
    {
      key: 'controls',
      header: '',
      align: 'right',
      render: (r) =>
        r.protected ? (
          <Badge variant="neutral">Protected</Badge>
        ) : (
          <Button variant="danger" size="sm" onClick={() => setDeleteRule(r)}>
            <Trash2 className="h-4 w-4" /> Delete
          </Button>
        ),
    },
  ]

  return (
    <div>
      <PageHeader title="Firewall" description="Manage UFW firewall rules for this server." icon={Flame}>
        <div className="flex items-center gap-3">
          <Badge variant={active ? 'success' : 'neutral'}>{active ? 'Active' : 'Inactive'}</Badge>
          {data && (active ? (
            <Button variant="danger" onClick={() => setToggleAction('disable')}>
              <ShieldOff className="h-4 w-4" /> Disable
            </Button>
          ) : (
            <Button variant="success" onClick={() => setToggleAction('enable')}>
              <ShieldCheck className="h-4 w-4" /> Enable
            </Button>
          ))}
          <Button onClick={() => setAddOpen(true)}>
            <Plus className="h-4 w-4" /> Add rule
          </Button>
          <Button variant="outline" asChild><a href="/api/v1/firewall/configuration/export" download><Download className="h-4 w-4" /> Export</a></Button>
          <Button variant="outline" onClick={() => setImportOpen(true)}><Upload className="h-4 w-4" /> Import</Button>
        </div>
      </PageHeader>

      {pending && (
        <Card className="mb-6 border-warning/50 bg-warning/5">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
            <div className="flex min-w-0 items-start gap-3">
              <TimerReset className="mt-0.5 h-5 w-5 shrink-0 text-warning" />
              <div>
                <div className="font-semibold">Confirm that you can still reach the server</div>
                <div className="text-sm text-muted-foreground">
                  {pending.summary}. Automatic rollback in <span className="font-semibold tabular-nums text-foreground">{secondsLeft}s</span>.
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" loading={revertMut.isPending} onClick={() => revertMut.mutate()}>
                <RotateCcw className="h-4 w-4" /> Revert now
              </Button>
              {confirmationToken && (
                <Button loading={confirmMut.isPending} onClick={() => confirmMut.mutate()}>
                  <ShieldCheck className="h-4 w-4" /> Keep change
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card><CardContent className="pt-5"><div className="text-sm text-muted-foreground">Inbound rules</div><div className="mt-1 text-2xl font-semibold">{(data?.rules || []).filter((row) => row.direction !== 'out').length}</div></CardContent></Card>
        <Card><CardContent className="pt-5"><div className="text-sm text-muted-foreground">Outbound rules</div><div className="mt-1 text-2xl font-semibold">{(data?.rules || []).filter((row) => row.direction === 'out').length}</div></CardContent></Card>
        <Card><CardContent className="pt-5"><div className="text-sm text-muted-foreground">Trusted networks</div><div className="mt-1 text-2xl font-semibold">{(data?.bypass || []).length}</div></CardContent></Card>
        <Card><CardContent className="pt-5"><div className="text-sm text-muted-foreground">Network policy</div><div className="mt-2 flex flex-wrap gap-2"><Badge variant="success">IPv4</Badge><Badge variant={data?.ipv6 ? 'success' : 'neutral'}>IPv6 {data?.ipv6 ? 'on' : 'off'}</Badge><Badge variant={data?.outbound_default === 'accept' ? 'success' : 'warning'}>Outbound {data?.outbound_default || 'unknown'}</Badge></div></CardContent></Card>
      </div>

      <Card className="mb-6"><CardHeader><CardTitle>Service presets</CardTitle><CardDescription>Add the complete rule set for a common service. The same connectivity confirmation and automatic rollback apply.</CardDescription></CardHeader><CardContent className="grid gap-3 md:grid-cols-[1fr_160px_1fr_auto] md:items-end">
        <FormField label="Service"><Select value={preset.preset_id} onChange={(event) => setPreset((value) => ({ ...value, preset_id: event.target.value }))}><option value="">Select preset</option>{(data?.presets || []).map((item) => <option key={item.id} value={item.id}>{item.label} · {item.direction === 'out' ? 'outbound' : 'inbound'}</option>)}</Select></FormField>
        <FormField label="Action"><Select value={preset.action} onChange={(event) => setPreset((value) => ({ ...value, action: event.target.value }))}><option value="allow">Allow</option><option value="deny">Block</option></Select></FormField>
        <FormField label="Address" hint="Optional source for inbound or destination for outbound."><Input value={preset.address} onChange={(event) => setPreset((value) => ({ ...value, address: event.target.value }))} placeholder="any" /></FormField>
        <Button disabled={!preset.preset_id} loading={presetMut.isPending} onClick={() => presetMut.mutate()}>Apply preset</Button>
      </CardContent></Card>

      <DataTable
        columns={columns}
        data={data?.rules}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        getRowKey={(r) => r.rule_id}
        filterable
        searchPlaceholder="Search rules…"
        pageSize={15}
        initialSort={{ key: 'port', dir: 'asc' }}
        emptyTitle="No firewall rules yet"
        emptyDescription="Add a rule to allow or deny traffic on a port."
        emptyIcon={Flame}
        emptyAction={<Button onClick={() => setAddOpen(true)}><Plus className="h-4 w-4" /> Add rule</Button>}
      />

      <Card className="mt-6">
        <CardHeader className="flex-row items-start justify-between gap-4">
          <div><CardTitle>Full-access IPs</CardTitle><CardDescription>Trusted IPs and CIDRs in this list can reach every server port. Boron inserts these rules before port blocks.</CardDescription></div>
          <Button variant="outline" onClick={() => setBypassOpen(true)}><Network className="h-4 w-4" /> Add trusted IP</Button>
        </CardHeader>
        <CardContent>
          {(data?.bypass || []).length ? <div className="divide-y divide-border rounded-panel border border-border">
            {data.bypass.map((entry) => <div key={entry.bypass_id} className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div><div className="font-mono text-sm font-medium">{entry.address}</div><div className="text-xs text-muted-foreground">{entry.label || 'Trusted administrator address'}</div></div>
              <Button variant="danger" size="sm" onClick={() => setDeleteBypass(entry)}><Trash2 className="h-4 w-4" /> Remove</Button>
            </div>)}
          </div> : <div className="rounded-panel border border-dashed border-border p-6 text-center text-sm text-muted-foreground">No full-access IPs configured.</div>}
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader className="flex-row items-start justify-between gap-4"><div><CardTitle>Temporary IP blocks</CardTitle><CardDescription>Time-bounded server-wide blocks expire automatically. Boron refuses the current administrator address and any network covered by a full-access bypass.</CardDescription></div><Button variant="outline" onClick={() => setBanOpen(true)}><Ban className="h-4 w-4" /> Block address</Button></CardHeader>
        <CardContent>{(data?.temporary_bans || []).length ? <div className="divide-y divide-border rounded-panel border border-border">{data.temporary_bans.map((entry) => <div key={entry.id} className="flex flex-wrap items-center justify-between gap-3 p-4"><div><div className="font-mono text-sm font-medium">{entry.value}</div><div className="text-xs text-muted-foreground">Until {new Date(entry.expires_at).toLocaleString()} · {entry.reason || 'No reason supplied'}</div></div><Button size="sm" variant="danger" onClick={() => setDeleteBan(entry)}>Unblock</Button></div>)}</div> : <div className="rounded-panel border border-dashed border-border p-6 text-center text-sm text-muted-foreground">No temporary blocks are active.</div>}</CardContent>
      </Card>

      <Card className="mt-6"><CardHeader><CardTitle><History className="h-5 w-5" /> Recent firewall changes</CardTitle><CardDescription>The complete request and result trail remains available in Audit Log.</CardDescription></CardHeader><CardContent>{(data?.recent_changes || []).length ? <div className="divide-y divide-border rounded-panel border border-border">{data.recent_changes.slice(0, 10).map((entry) => <div key={entry.id} className="grid gap-1 p-3 text-sm sm:grid-cols-[180px_1fr_auto]"><span className="text-muted-foreground">{new Date(entry.created_at).toLocaleString()}</span><span>{entry.operation} · {entry.actor}</span><Badge variant={entry.result === 'ok' ? 'success' : 'danger'}>{entry.result}</Badge></div>)}</div> : <p className="text-sm text-muted-foreground">No recorded firewall changes yet.</p>}</CardContent></Card>

      {/* Add rule dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Add firewall rule</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              addMut.mutate({
                action: form.action,
                port: Number(form.port),
                protocol: form.protocol,
                direction: form.direction,
                from_addr: form.from_addr.trim() || 'any',
                to_addr: form.to_addr.trim() || 'any',
                comment: form.comment.trim(),
              })
            }}
          >
            <DialogBody className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <FormField label="Action" required>
                  <Select value={form.action} onChange={(e) => setForm((f) => ({ ...f, action: e.target.value }))}>
                    <option value="allow">allow</option>
                    <option value="deny">deny</option>
                  </Select>
                </FormField>
                <FormField label="Protocol" required>
                  <Select value={form.protocol} onChange={(e) => setForm((f) => ({ ...f, protocol: e.target.value }))}>
                    <option value="any">any</option>
                    <option value="tcp">tcp</option>
                    <option value="udp">udp</option>
                  </Select>
                </FormField>
              </div>
              <FormField label="Direction" required><Select value={form.direction} onChange={(e) => setForm((f) => ({ ...f, direction: e.target.value }))}><option value="in">Inbound to this server</option><option value="out">Outbound from this server</option></Select></FormField>
              <FormField label="Port" required hint="Between 1 and 65535.">
                <Input
                  type="number"
                  min="1"
                  max="65535"
                  value={form.port}
                  onChange={(e) => setForm((f) => ({ ...f, port: e.target.value }))}
                  placeholder="443"
                  required
                  autoFocus
                />
              </FormField>
              <FormField label={form.direction === 'out' ? 'Destination address' : 'Source address'} hint={`IP or CIDR. Leave blank for any ${form.direction === 'out' ? 'destination' : 'source'}.`}>
                <Input
                  value={form.direction === 'out' ? form.to_addr : form.from_addr}
                  onChange={(e) => setForm((f) => ({ ...f, [form.direction === 'out' ? 'to_addr' : 'from_addr']: e.target.value }))}
                  placeholder="any"
                />
              </FormField>
              {form.direction === 'out' && form.action === 'deny' && <p className="rounded-btn border border-warning/40 bg-warning/5 p-3 text-sm text-muted-foreground">Outbound blocks can interrupt DNS, package updates, backups, mail delivery, ACME, Cloudflare and external APIs. Review the selected port before applying.</p>}
              <FormField label="Comment" hint="Optional label for this rule.">
                <Input
                  value={form.comment}
                  onChange={(e) => setForm((f) => ({ ...f, comment: e.target.value }))}
                  placeholder="Allow HTTPS"
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setAddOpen(false)}>Cancel</Button>
              <Button type="submit" loading={addMut.isPending}>Add rule</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={bypassOpen} onOpenChange={setBypassOpen}>
        <DialogContent size="sm"><DialogHeader><DialogTitle>Add full-access IP</DialogTitle></DialogHeader>
          <form onSubmit={(event) => { event.preventDefault(); addBypassMut.mutate({ address: bypassForm.address.trim(), label: bypassForm.label.trim() }) }}>
            <DialogBody className="space-y-4">
              <p className="text-sm text-muted-foreground">Use this for a trusted office, VPN, or recovery address that must remain reachable even when a port is blocked.</p>
              <FormField label="IP address or CIDR" required><Input required autoFocus value={bypassForm.address} onChange={(event) => setBypassForm((value) => ({ ...value, address: event.target.value }))} placeholder="203.0.113.10" /></FormField>
              <FormField label="Label" hint="Letters, numbers, spaces, dots, underscores, and hyphens."><Input value={bypassForm.label} onChange={(event) => setBypassForm((value) => ({ ...value, label: event.target.value }))} placeholder="Office VPN" /></FormField>
            </DialogBody>
            <DialogFooter><Button type="button" variant="secondary" onClick={() => setBypassOpen(false)}>Cancel</Button><Button type="submit" loading={addBypassMut.isPending}>Add trusted IP</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={banOpen} onOpenChange={setBanOpen}><DialogContent size="sm"><DialogHeader><DialogTitle>Temporarily block an address</DialogTitle></DialogHeader>
        <form onSubmit={(event) => { event.preventDefault(); banMut.mutate() }}><DialogBody className="space-y-4">
          <FormField label="IP address or CIDR" required><Input required autoFocus value={banForm.value} onChange={(event) => setBanForm((value) => ({ ...value, value: event.target.value }))} placeholder="198.51.100.25" /></FormField>
          <FormField label="Duration"><Select value={banForm.duration_minutes} onChange={(event) => setBanForm((value) => ({ ...value, duration_minutes: Number(event.target.value) }))}><option value="15">15 minutes</option><option value="60">1 hour</option><option value="360">6 hours</option><option value="1440">1 day</option><option value="10080">7 days</option><option value="43200">30 days</option></Select></FormField>
          <FormField label="Reason"><Input value={banForm.reason} onChange={(event) => setBanForm((value) => ({ ...value, reason: event.target.value }))} placeholder="Repeated malicious requests" /></FormField>
        </DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setBanOpen(false)}>Cancel</Button><Button type="submit" variant="danger" loading={banMut.isPending}>Block temporarily</Button></DialogFooter></form>
      </DialogContent></Dialog>

      <Dialog open={importOpen} onOpenChange={(open) => { setImportOpen(open); if (!open) setImportPreview(null) }}><DialogContent><DialogHeader><DialogTitle>Import firewall configuration</DialogTitle></DialogHeader><DialogBody className="space-y-4">
        <FormField label="Boron firewall JSON" hint="Preview validates every entry and shows the exact additions and removals before applying."><Input type="file" accept="application/json,.json" onChange={(event) => { const file = event.target.files?.[0]; if (file) file.text().then((value) => { setImportText(value); setImportPreview(null) }) }} /></FormField>
        <label className="flex items-start gap-3 rounded-btn border border-border p-3 text-sm"><input type="checkbox" className="mt-1" checked={replaceImport} onChange={(event) => { setReplaceImport(event.target.checked); setImportPreview(null) }} /><span><span className="block font-medium">Replace manageable rules</span><span className="text-muted-foreground">Rules absent from the file are removed after imported rules are added. Unknown UFW rule types remain untouched.</span></span></label>
        {importPreview && <div className="grid grid-cols-2 gap-3 rounded-btn bg-muted p-4 text-sm sm:grid-cols-4">{Object.entries(importPreview.summary).map(([key, value]) => <div key={key}><div className="text-muted-foreground">{key.replaceAll('_', ' ')}</div><div className="text-xl font-semibold">{value}</div></div>)}</div>}
      </DialogBody><DialogFooter><Button variant="secondary" onClick={() => setImportOpen(false)}>Cancel</Button><Button variant="outline" disabled={!importText} loading={previewImportMut.isPending} onClick={() => previewImportMut.mutate()}>Preview</Button><Button disabled={!importPreview} loading={importMut.isPending} onClick={() => importMut.mutate()}>Apply import</Button></DialogFooter></DialogContent></Dialog>

      {/* Enable / disable confirmation */}
      <ConfirmDialog
        open={!!toggleAction}
        onOpenChange={(o) => !o && setToggleAction(null)}
        title={toggleAction === 'disable' ? 'Disable firewall?' : 'Enable firewall?'}
        description={
          toggleAction === 'disable'
            ? 'This disables all UFW firewall protection and exposes every port. Are you sure?'
            : 'This enables UFW enforcement. SSH, panel, web, and mail ports are auto-allowed first.'
        }
        confirmLabel={toggleAction === 'disable' ? 'Disable UFW' : 'Enable UFW'}
        variant={toggleAction === 'disable' ? 'danger' : 'primary'}
        loading={toggleMut.isPending}
        onConfirm={() => toggleMut.mutate(toggleAction)}
      />
      <ConfirmDialog open={!!deleteBypass} onOpenChange={(open) => !open && setDeleteBypass(null)} title="Remove full-access IP?" description={`Traffic from ${deleteBypass?.address || 'this address'} will follow the normal firewall rules again.`} confirmLabel="Remove trusted IP" variant="danger" loading={deleteBypassMut.isPending} onConfirm={() => deleteBypassMut.mutate(deleteBypass.bypass_id)} />
      <ConfirmDialog open={!!deleteBan} onOpenChange={(open) => !open && setDeleteBan(null)} title="Remove temporary block?" description={`${deleteBan?.value || 'This address'} will be able to connect according to the remaining firewall rules.`} confirmLabel="Unblock address" variant="danger" loading={deleteBanMut.isPending} onConfirm={() => deleteBanMut.mutate(deleteBan.id)} />

      {/* Delete rule confirmation */}
      <ConfirmDialog
        open={!!deleteRule}
        onOpenChange={(o) => !o && setDeleteRule(null)}
        title="Delete firewall rule?"
        description={
          deleteRule
            ? `Remove the ${deleteRule.action} rule on port ${deleteRule.port}/${deleteRule.protocol}` +
              (deleteRule.from && deleteRule.from !== 'any' ? ` from ${deleteRule.from}.` : '.')
            : ''
        }
        confirmLabel="Delete rule"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(deleteRule.rule_id)}
      />
    </div>
  )
}
