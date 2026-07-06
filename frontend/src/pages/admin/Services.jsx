import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Cog, MoreHorizontal, Play, Square, RotateCw, ScrollText, RefreshCw,
} from 'lucide-react'
import { get, post } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Badge } from '@/components/ui/Badge'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
  DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { toast } from '@/components/ui/Toast'

const ACTION_VERBS = { start: 'started', stop: 'stopped', restart: 'restarted' }
// stop/restart briefly (or fully) interrupt the service, so they require an
// explicit confirmation — the daemon also enforces confirm=true for these.
const CONFIRM_ACTIONS = new Set(['stop', 'restart'])
const CONFIRM_COPY = {
  stop: (s) => `This stops ${s} until it is started again manually. Anything it serves will be unavailable in the meantime.`,
  restart: (s) => `This briefly interrupts ${s} while it restarts.`,
}

export default function Services() {
  // Services are host-wide (admin only) and carry no {u}, but the hook works for
  // admin too and keeps the query keyed to the acting identity.
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState(null) // { service, action }
  const [logsService, setLogsService] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['services', username],
    queryFn: () => get('/api/v1/services'),
    // Poll so active/enabled state stays live as services are controlled.
    refetchInterval: 10000,
  })

  const actionMut = useMutation({
    // Always send confirm=true: the daemon ignores it for start/reload and
    // requires it for stop/restart (which we already gate behind ConfirmDialog).
    mutationFn: ({ service, action }) => post(`/api/v1/services/${service}/${action}`, { confirm: true }),
    onSuccess: (_res, { service, action }) => {
      toast.success(`Service ${ACTION_VERBS[action] || action}`, service)
      qc.invalidateQueries({ queryKey: ['services', username] })
      setConfirm(null)
    },
    onError: (e) => toast.error('Action failed', e.message),
  })

  function runAction(service, action) {
    if (CONFIRM_ACTIONS.has(action)) setConfirm({ service, action })
    else actionMut.mutate({ service, action })
  }

  const columns = [
    {
      key: 'service',
      header: 'Service',
      sortable: true,
      searchable: true,
      searchValue: (r) => `${r.service} ${r.unit || ''}`,
      render: (r) => (
        <div>
          <div className="font-medium text-foreground">{r.service}</div>
          {r.unit && <div className="text-xs text-muted-foreground">{r.unit}</div>}
        </div>
      ),
    },
    {
      key: 'active',
      header: 'Active',
      sortable: true,
      sortValue: (r) => (r.active === 'active' ? 0 : 1),
      render: (r) => <StatusBadge status={r.active === 'active' ? 'active' : 'stopped'} />,
    },
    {
      key: 'enabled',
      header: 'Enabled',
      sortable: true,
      render: (r) => (
        <Badge variant={r.enabled === 'enabled' ? 'success' : 'neutral'} className="capitalize">
          {r.enabled}
        </Badge>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => {
        const running = r.active === 'active'
        return (
          <div className="flex justify-end" onClick={(e) => e.stopPropagation()}>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.service}`}>
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuItem disabled={running} onSelect={() => runAction(r.service, 'start')}>
                  <Play className="h-4 w-4" /> Start
                </DropdownMenuItem>
                <DropdownMenuItem disabled={!running} onSelect={() => runAction(r.service, 'stop')}>
                  <Square className="h-4 w-4" /> Stop
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => runAction(r.service, 'restart')}>
                  <RotateCw className="h-4 w-4" /> Restart
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => setLogsService(r.service)}>
                  <ScrollText className="h-4 w-4" /> Logs
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )
      },
    },
  ]

  return (
    <div>
      <PageHeader
        title="Services"
        description="Start, stop, and inspect the hosting stack's system services."
        icon={Cog}
      />

      <DataTable
        columns={columns}
        data={data?.services}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search services…"
        pageSize={0}
        initialSort={{ key: 'service', dir: 'asc' }}
        getRowKey={(r) => r.service}
        emptyTitle="No services"
        emptyDescription="No manageable services are registered on this server."
        emptyIcon={Cog}
      />

      {/* Confirmation for stop / restart */}
      <ConfirmDialog
        open={!!confirm}
        onOpenChange={(v) => { if (!v) setConfirm(null) }}
        title={confirm ? `${confirm.action === 'stop' ? 'Stop' : 'Restart'} ${confirm.service}?` : 'Confirm action'}
        description={confirm ? CONFIRM_COPY[confirm.action]?.(confirm.service) : undefined}
        confirmLabel={confirm?.action === 'stop' ? 'Stop service' : 'Restart service'}
        variant={confirm?.action === 'stop' ? 'danger' : 'warning'}
        loading={actionMut.isPending}
        onConfirm={() => confirm && actionMut.mutate(confirm)}
      />

      {/* Logs dialog */}
      <LogsDialog service={logsService} onOpenChange={(v) => { if (!v) setLogsService(null) }} />
    </div>
  )
}

function LogsDialog({ service, onOpenChange }) {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['service-detail', service],
    queryFn: () => get(`/api/v1/services/${service}`),
    enabled: !!service,
  })
  const lines = data?.log_lines || []

  return (
    <Dialog open={!!service} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Logs — {service}</DialogTitle>
          <DialogDescription>Last 50 log lines from the service's systemd unit.</DialogDescription>
        </DialogHeader>
        <DialogBody>
          {isLoading ? (
            <CenteredSpinner />
          ) : error ? (
            <div className="text-sm text-danger">{error.message}</div>
          ) : lines.length === 0 ? (
            <div className="rounded-btn border border-border bg-muted/40 px-4 py-8 text-center text-sm text-muted-foreground">
              No log output.
            </div>
          ) : (
            <pre className="max-h-[55vh] overflow-auto rounded-btn border border-border bg-muted/40 p-4 font-mono text-xs leading-relaxed text-foreground whitespace-pre-wrap">
              {lines.join('\n')}
            </pre>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={() => refetch()} loading={isFetching}>
            <RefreshCw className="h-4 w-4" /> Refresh
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
