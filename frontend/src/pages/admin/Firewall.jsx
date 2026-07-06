import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Flame, Plus, Trash2, ShieldCheck, ShieldOff } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
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
