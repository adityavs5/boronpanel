import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, Plus, Trash2 } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

const EMPTY_FORM = { value: '', reason: '' }

// QA round 2, item 14: permanent, server-wide IP/CIDR bans (UFW `deny from
// <ip>`, evaluated ahead of other rules) -- distinct from the per-domain IP
// blocker (a domain's own Security tab) and from Fail2ban's automatic,
// time-bounded jails (which only ever expose unban, never a manual/
// permanent ban).
export default function IpBans() {
  const qc = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [toRemove, setToRemove] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['ip-bans'],
    queryFn: () => get('/api/v1/admin/ip-bans'),
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['ip-bans'] })

  const addMut = useMutation({
    mutationFn: (body) => post('/api/v1/admin/ip-bans', body),
    onSuccess: () => {
      toast.success('IP banned', 'Blocked server-wide, effective immediately.')
      invalidate()
      setAddOpen(false)
      setForm(EMPTY_FORM)
    },
    onError: (e) => toast.error('Could not add ban', e.message),
  })

  const removeMut = useMutation({
    mutationFn: (id) => del(`/api/v1/admin/ip-bans/${id}`),
    onSuccess: () => {
      toast.success('Ban removed')
      invalidate()
      setToRemove(null)
    },
    onError: (e) => toast.error('Could not remove ban', e.message),
  })

  const columns = [
    { key: 'value', header: 'IP / CIDR', sortable: true, searchable: true, render: (r) => <span className="font-mono text-sm text-foreground">{r.value}</span> },
    { key: 'reason', header: 'Reason', searchable: true, render: (r) => r.reason || <span className="text-muted-foreground">—</span> },
    { key: 'banned_by', header: 'Banned by', sortable: true, searchable: true },
    { key: 'created_at', header: 'Banned', sortable: true, render: (r) => (r.created_at ? formatDate(r.created_at) : '—') },
    {
      key: 'controls',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => (
        <Button variant="danger" size="sm" onClick={() => setToRemove(r)}>
          <Trash2 className="h-4 w-4" /> Remove
        </Button>
      ),
    },
  ]

  return (
    <div>
      <PageHeader title="IP Bans" description="Permanently block an IP address or CIDR range from the entire server (all ports, all services)." icon={Ban}>
        <Button onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4" /> Ban IP
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={data?.bans}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        getRowKey={(r) => r.id}
        filterable
        searchPlaceholder="Search bans…"
        pageSize={20}
        initialSort={{ key: 'created_at', dir: 'desc' }}
        emptyTitle="No permanent bans"
        emptyDescription="Block an abusive IP or CIDR range server-wide, across every port and service."
        emptyIcon={Ban}
        emptyAction={<Button onClick={() => setAddOpen(true)}><Plus className="h-4 w-4" /> Ban IP</Button>}
      />

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Ban an IP or CIDR</DialogTitle>
            <DialogDescription>
              Blocks all traffic from this address server-wide, immediately and permanently, until removed.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              addMut.mutate({ value: form.value.trim(), reason: form.reason.trim() })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="IP or CIDR" required hint="e.g. 203.0.113.5 or 203.0.113.0/24">
                <Input
                  autoFocus
                  value={form.value}
                  onChange={(e) => setForm((f) => ({ ...f, value: e.target.value }))}
                  placeholder="203.0.113.5"
                  required
                  className="font-mono"
                />
              </FormField>
              <FormField label="Reason" hint="Optional — shown in the ban list.">
                <Input
                  value={form.reason}
                  onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
                  placeholder="Repeated login abuse"
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setAddOpen(false)}>Cancel</Button>
              <Button type="submit" loading={addMut.isPending} disabled={!form.value.trim()}>Ban IP</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toRemove}
        onOpenChange={(v) => { if (!v) setToRemove(null) }}
        title={toRemove ? `Remove ban on ${toRemove.value}?` : 'Remove ban?'}
        description="This address will be able to reach the server again immediately."
        confirmLabel="Remove ban"
        loading={removeMut.isPending}
        onConfirm={() => toRemove && removeMut.mutate(toRemove.id)}
      />
    </div>
  )
}
