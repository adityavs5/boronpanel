import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Inbox, MoreHorizontal, Send, Trash2 } from 'lucide-react'
import { get, post } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatBytes, formatDuration, truncate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { ConfirmDialog } from '@/components/ui/Dialog'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'
import { toast } from '@/components/ui/Toast'

export default function MailQueue() {
  // Works for both the global admin view and any per-account context.
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [deleteAllOpen, setDeleteAllOpen] = useState(false)
  const [toDelete, setToDelete] = useState(null) // row pending delete

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['mail-queue', username],
    queryFn: () => get('/api/v1/mail-queue'),
    refetchInterval: 10000,
  })

  const entries = data?.entries || []
  const count = data?.count ?? entries.length

  const invalidate = () => qc.invalidateQueries({ queryKey: ['mail-queue', username] })

  const flushAllMut = useMutation({
    mutationFn: () => post('/api/v1/mail-queue/flush'),
    onSuccess: () => { toast.success('Queue flushed', 'Postfix is attempting to deliver all queued mail now.'); invalidate() },
    onError: (e) => toast.error('Could not flush queue', e.message),
  })

  const deleteAllMut = useMutation({
    mutationFn: () => post('/api/v1/mail-queue/delete-all'),
    onSuccess: () => { toast.success('Queue emptied', 'Every message was removed from the mail queue.'); invalidate(); setDeleteAllOpen(false) },
    onError: (e) => toast.error('Could not empty queue', e.message),
  })

  const flushMut = useMutation({
    mutationFn: (row) => post(`/api/v1/mail-queue/${row.queue_id}/flush`),
    onSuccess: (_res, row) => { toast.success('Message flushed', `Delivery of ${row.queue_id} was requested.`); invalidate() },
    onError: (e) => toast.error('Could not flush message', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (row) => post(`/api/v1/mail-queue/${row.queue_id}/delete`),
    onSuccess: (_res, row) => { toast.success('Message deleted', `${row.queue_id} was removed from the queue.`); invalidate(); setToDelete(null) },
    onError: (e) => toast.error('Could not delete message', e.message),
  })

  const columns = [
    {
      key: 'queue_id',
      header: 'Queue ID',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-mono text-sm text-foreground">{r.queue_id}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      render: (r) => <StatusBadge status={r.status} />,
    },
    {
      key: 'sender',
      header: 'Sender',
      sortable: true,
      searchable: true,
      render: (r) => r.sender || <span className="text-muted-foreground">—</span>,
    },
    {
      key: 'recipient',
      header: 'Recipient',
      sortable: true,
      searchable: true,
      render: (r) => r.recipient || <span className="text-muted-foreground">—</span>,
    },
    {
      key: 'size',
      header: 'Size',
      align: 'right',
      sortable: true,
      sortValue: (r) => r.size ?? 0,
      render: (r) => <span className="tabular-nums text-muted-foreground">{formatBytes(r.size)}</span>,
    },
    {
      key: 'age',
      header: 'Age',
      align: 'right',
      sortable: true,
      sortValue: (r) => r.age_seconds ?? 0,
      render: (r) => <span className="tabular-nums text-muted-foreground">{formatDuration(r.age_seconds)}</span>,
    },
    {
      key: 'defer_reason',
      header: 'Defer reason',
      searchable: true,
      render: (r) =>
        r.defer_reason
          ? <span className="text-muted-foreground" title={r.defer_reason}>{truncate(r.defer_reason, 48)}</span>
          : <span className="text-muted-foreground">—</span>,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => (
        <div className="flex justify-end">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.queue_id}`}>
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onSelect={() => flushMut.mutate(r)}>
                <Send className="h-4 w-4" /> Flush
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setToDelete(r)}>
                <Trash2 className="h-4 w-4" /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Mail queue"
        description={`${count} message${count === 1 ? '' : 's'} currently in the Postfix queue. The list refreshes automatically.`}
        icon={Inbox}
      >
        <Button variant="secondary" loading={flushAllMut.isPending} disabled={count === 0} onClick={() => flushAllMut.mutate()}>
          <Send className="h-4 w-4" /> Flush all
        </Button>
        <Button variant="danger" disabled={count === 0} onClick={() => setDeleteAllOpen(true)}>
          <Trash2 className="h-4 w-4" /> Delete all
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={entries}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search by sender or recipient…"
        pageSize={20}
        initialSort={{ key: 'age', dir: 'desc' }}
        getRowKey={(r) => r.queue_id}
        emptyTitle="Queue is empty"
        emptyDescription="There are no messages waiting to be delivered."
        emptyIcon={Inbox}
      />

      {/* Delete all messages */}
      <ConfirmDialog
        open={deleteAllOpen}
        onOpenChange={setDeleteAllOpen}
        title="Empty the mail queue?"
        description="Every message in the queue is permanently deleted and will never be delivered. This cannot be undone."
        confirmLabel="Delete all messages"
        loading={deleteAllMut.isPending}
        onConfirm={() => deleteAllMut.mutate()}
      />

      {/* Delete a single message */}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => { if (!v) setToDelete(null) }}
        title={toDelete ? `Delete ${toDelete.queue_id}?` : 'Delete message?'}
        description="This message is permanently removed from the queue and will never be delivered. This cannot be undone."
        confirmLabel="Delete message"
        loading={deleteMut.isPending}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />
    </div>
  )
}
