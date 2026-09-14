import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Webhook, Plus, Trash2, Send, ScrollText } from 'lucide-react'
import { get, post, patch, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatDate } from '@/lib/utils'
import { WEBHOOK_EVENTS } from '@/config/constants'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Switch, Checkbox } from '@/components/ui/Toggle'
import { Input, FormField } from '@/components/ui/Input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

const EMPTY_FORM = { url: '', secret: '', events: [] }

export default function Webhooks() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [deliveriesFor, setDeliveriesFor] = useState(null)

  const invalidate = () => qc.invalidateQueries({ queryKey: ['webhooks', username] })

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['webhooks', username],
    queryFn: () => get('/api/v1/admin/webhooks'),
  })

  const deliveriesQuery = useQuery({
    queryKey: ['webhook-deliveries', deliveriesFor?.id],
    queryFn: () => get(`/api/v1/admin/webhooks/${deliveriesFor.id}/deliveries`),
    enabled: !!deliveriesFor,
  })

  const createMut = useMutation({
    mutationFn: (body) => post('/api/v1/admin/webhooks', body),
    onSuccess: () => {
      toast.success('Webhook created')
      invalidate()
      setCreateOpen(false)
      setForm(EMPTY_FORM)
    },
    onError: (e) => toast.error('Could not create webhook', e.message),
  })

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }) => patch(`/api/v1/admin/webhooks/${id}`, { enabled }),
    onSuccess: (_res, { enabled }) => {
      toast.success(enabled ? 'Webhook enabled' : 'Webhook disabled')
      invalidate()
    },
    onError: (e) => toast.error('Could not update webhook', e.message),
  })

  const testMut = useMutation({
    mutationFn: (id) => post(`/api/v1/admin/webhooks/${id}/test`),
    onSuccess: () => toast.success('Test event queued', 'A test payload was dispatched to the endpoint.'),
    onError: (e) => toast.error('Could not send test event', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (id) => del(`/api/v1/admin/webhooks/${id}`),
    onSuccess: () => {
      toast.success('Webhook deleted')
      invalidate()
      setDeleteTarget(null)
    },
    onError: (e) => toast.error('Could not delete webhook', e.message),
  })

  const toggleEvent = (ev) =>
    setForm((f) => ({
      ...f,
      events: f.events.includes(ev) ? f.events.filter((x) => x !== ev) : [...f.events, ev],
    }))

  const columns = [
    {
      key: 'url',
      header: 'Endpoint URL',
      sortable: true,
      searchable: true,
      render: (r) => (
        <span className="block max-w-[280px] truncate font-medium text-foreground" title={r.url}>
          {r.url}
        </span>
      ),
    },
    {
      key: 'events',
      header: 'Events',
      searchable: true,
      searchValue: (r) => (r.events || []).join(' '),
      render: (r) =>
        r.events?.length ? (
          <div className="flex flex-wrap gap-1">
            {r.events.map((ev) => (
              <Badge key={ev} variant="accent">{ev}</Badge>
            ))}
          </div>
        ) : (
          <span className="text-muted-foreground">No events selected</span>
        ),
    },
    {
      key: 'enabled',
      header: 'Enabled',
      sortable: true,
      render: (r) => (
        <Switch
          checked={!!r.enabled}
          disabled={toggleMut.isPending && toggleMut.variables?.id === r.id}
          onCheckedChange={(next) => toggleMut.mutate({ id: r.id, enabled: next })}
          aria-label={`Toggle webhook ${r.url}`}
        />
      ),
    },
    {
      key: 'created_at',
      header: 'Created',
      sortable: true,
      render: (r) => (r.created_at ? formatDate(r.created_at) : '—'),
    },
    {
      key: 'controls',
      header: '',
      align: 'right',
      render: (r) => (
        <div className="flex items-center justify-end gap-2">
          <Button
            variant="secondary"
            size="sm"
            loading={testMut.isPending && testMut.variables === r.id}
            onClick={() => testMut.mutate(r.id)}
          >
            <Send className="h-4 w-4" /> Test
          </Button>
          <Button variant="outline" size="sm" onClick={() => setDeliveriesFor(r)}>
            <ScrollText className="h-4 w-4" /> Deliveries
          </Button>
          <Button variant="danger" size="sm" onClick={() => setDeleteTarget(r)}>
            <Trash2 className="h-4 w-4" /> Delete
          </Button>
        </div>
      ),
    },
  ]

  const deliveryColumns = [
    { key: 'event', header: 'Event', searchable: true, render: (d) => <Badge variant="accent">{d.event}</Badge> },
    { key: 'status', header: 'Status', sortable: true, render: (d) => <StatusBadge status={d.status} /> },
    {
      key: 'response_code',
      header: 'Response',
      align: 'right',
      render: (d) =>
        d.response_code != null ? (
          <span className="tabular-nums">{d.response_code}</span>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    { key: 'attempt_count', header: 'Attempts', align: 'right', render: (d) => <span className="tabular-nums">{d.attempt_count ?? '—'}</span> },
    {
      key: 'error',
      header: 'Error',
      searchable: true,
      render: (d) =>
        d.error ? (
          <span className="block max-w-[220px] truncate text-danger" title={d.error}>{d.error}</span>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    { key: 'created_at', header: 'Time', sortable: true, render: (d) => (d.created_at ? formatDate(d.created_at) : '—') },
  ]

  return (
    <div>
      <PageHeader
        title="Webhooks"
        description="POST a signed JSON payload to an external URL on account lifecycle events. Deliveries retry with backoff and every attempt is logged."
        icon={Webhook}
      >
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Add webhook
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={data?.webhooks}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search webhooks…"
        pageSize={15}
        getRowKey={(r) => r.id}
        emptyTitle="No webhooks configured"
        emptyDescription="Add a webhook to receive real-time event notifications at your own endpoint."
        emptyIcon={Webhook}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Add webhook</Button>}
      />

      {/* Create webhook dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Add a webhook</DialogTitle>
            <DialogDescription>
              Payloads are signed with HMAC-SHA256 over the raw request body (header X-Boron-Signature).
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({
                url: form.url.trim(),
                events: form.events,
                secret: form.secret.trim() || undefined,
                enabled: true,
              })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Endpoint URL" required error={createMut.error?.fields?.url}>
                <Input
                  autoFocus
                  type="url"
                  value={form.url}
                  onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
                  placeholder="https://example.com/webhook"
                  required
                />
              </FormField>
              <FormField label="Secret" hint="Leave blank to auto-generate a signing secret.">
                <Input
                  value={form.secret}
                  onChange={(e) => setForm((f) => ({ ...f, secret: e.target.value }))}
                  placeholder="Optional"
                />
              </FormField>
              <FormField label="Events" hint="Select at least one event to deliver.">
                <div className="space-y-2 rounded-btn border border-border p-3">
                  {WEBHOOK_EVENTS.map((ev) => (
                    <label key={ev} className="flex cursor-pointer items-center gap-2 text-sm text-foreground">
                      <Checkbox
                        checked={form.events.includes(ev)}
                        onCheckedChange={() => toggleEvent(ev)}
                      />
                      <code className="text-xs">{ev}</code>
                    </label>
                  ))}
                </div>
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending} disabled={!form.events.length}>Add webhook</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Deliveries dialog */}
      <Dialog open={!!deliveriesFor} onOpenChange={(o) => !o && setDeliveriesFor(null)}>
        <DialogContent size="xl">
          <DialogHeader>
            <DialogTitle>Recent deliveries</DialogTitle>
            {deliveriesFor && <DialogDescription className="break-all">{deliveriesFor.url}</DialogDescription>}
          </DialogHeader>
          <DialogBody>
            <DataTable
              columns={deliveryColumns}
              data={deliveriesQuery.data?.deliveries}
              loading={deliveriesQuery.isLoading}
              error={deliveriesQuery.error}
              onRetry={deliveriesQuery.refetch}
              getRowKey={(d, i) => d.id ?? i}
              pageSize={10}
              emptyTitle="No deliveries yet"
              emptyDescription="This webhook has not fired any events. Use Test to send a sample payload."
              emptyIcon={ScrollText}
            />
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => setDeliveriesFor(null)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title="Delete webhook?"
        description={
          deleteTarget
            ? `Stop delivering events to ${deleteTarget.url}. Existing delivery history will be removed.`
            : ''
        }
        confirmLabel="Delete webhook"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(deleteTarget.id)}
      />
    </div>
  )
}
