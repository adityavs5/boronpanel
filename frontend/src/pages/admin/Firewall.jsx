import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Flame, Plus, Trash2, ShieldCheck, ShieldOff, Network } from 'lucide-react'
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

const EMPTY_FORM = { action: 'allow', port: '', protocol: 'any', from_addr: '', comment: '' }

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

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['firewall-rules', username],
    queryFn: () => get('/api/v1/firewall/rules'),
  })

  const active = data?.active
  const invalidate = () => qc.invalidateQueries({ queryKey: ['firewall-rules', username] })

  const toggleMut = useMutation({
    mutationFn: (action) => post(`/api/v1/firewall/${action}`, { confirm: true }),
    onSuccess: (_res, action) => {
      toast.success(action === 'enable' ? 'Firewall enabled' : 'Firewall disabled')
      invalidate()
      setToggleAction(null)
    },
    onError: (e) => toast.error('Action failed', e.message),
  })

  const addMut = useMutation({
    mutationFn: (body) => post('/api/v1/firewall/rules', body),
    onSuccess: () => {
      toast.success('Rule added')
      invalidate()
      setAddOpen(false)
      setForm(EMPTY_FORM)
    },
    onError: (e) => toast.error('Could not add rule', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (ruleId) => del(`/api/v1/firewall/rules/${ruleId}`),
    onSuccess: () => {
      toast.success('Rule deleted')
      invalidate()
      setDeleteRule(null)
    },
    onError: (e) => toast.error('Could not delete rule', e.message),
  })

  const addBypassMut = useMutation({
    mutationFn: (body) => post('/api/v1/firewall/bypass', body),
    onSuccess: () => {
      toast.success('Full-access IP added')
      invalidate()
      setBypassOpen(false)
      setBypassForm({ address: '', label: '' })
    },
    onError: (e) => toast.error('Could not add bypass IP', e.message),
  })

  const deleteBypassMut = useMutation({
    mutationFn: (id) => del(`/api/v1/firewall/bypass/${id}`),
    onSuccess: () => {
      toast.success('Full-access IP removed')
      invalidate()
      setDeleteBypass(null)
    },
    onError: (e) => toast.error('Could not remove bypass IP', e.message),
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
      key: 'port',
      header: 'Port',
      sortable: true,
      searchable: true,
      render: (r) => <span className="tabular-nums">{r.port}</span>,
    },
    { key: 'protocol', header: 'Protocol', sortable: true, render: (r) => r.protocol },
    { key: 'from', header: 'From', searchable: true, render: (r) => r.from || 'any' },
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
        </div>
      </PageHeader>

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
                from_addr: form.from_addr.trim() || 'any',
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
              <FormField label="From address" hint="IP or CIDR. Leave blank to allow from any source.">
                <Input
                  value={form.from_addr}
                  onChange={(e) => setForm((f) => ({ ...f, from_addr: e.target.value }))}
                  placeholder="any"
                />
              </FormField>
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
