import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ShieldAlert, ShieldOff, Ban, Eye, Wrench, History } from 'lucide-react'
import { get, post } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

export default function Fail2ban() {
  // useAccountUsername keeps the query key stable across admin / per-account contexts.
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [detailJail, setDetailJail] = useState(null) // jail NAME whose banned IPs are shown
  const [unbanAllJail, setUnbanAllJail] = useState(null) // jail NAME pending unban-all
  const [bootstrapOpen, setBootstrapOpen] = useState(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['fail2ban', username],
    queryFn: () => get('/api/v1/fail2ban'),
    refetchInterval: 15000,
  })

  const events = useQuery({
    queryKey: ['fail2ban-events', username],
    queryFn: () => get('/api/v1/fail2ban/events?limit=50'),
    refetchInterval: 15000,
  })

  const jails = data?.jails || []
  // Derive the open dialog's jail from live query data so it reflects polling + mutations.
  const activeJail = jails.find((j) => j.jail === detailJail) || null

  const invalidate = () => qc.invalidateQueries({ queryKey: ['fail2ban', username] })

  const bootstrapMut = useMutation({
    mutationFn: () => post('/api/v1/fail2ban/bootstrap'),
    onSuccess: () => {
      toast.success('Jails configured', 'Fail2ban jails were installed / reconfigured.')
      invalidate()
      setBootstrapOpen(false)
    },
    onError: (e) => toast.error('Could not configure jails', e.message),
  })

  const unbanMut = useMutation({
    mutationFn: ({ jail, ip }) => post(`/api/v1/fail2ban/${encodeURIComponent(jail)}/unban`, { ip }),
    onSuccess: (_res, { ip }) => {
      toast.success('IP unbanned', `${ip} was removed from the jail.`)
      invalidate()
    },
    onError: (e) => toast.error('Could not unban IP', e.message),
  })

  const unbanAllMut = useMutation({
    mutationFn: (jail) => post(`/api/v1/fail2ban/${encodeURIComponent(jail)}/unban-all`),
    onSuccess: (_res, jail) => {
      toast.success('All IPs unbanned', `Every banned IP was cleared from ${jail}.`)
      invalidate()
      setUnbanAllJail(null)
    },
    onError: (e) => toast.error('Could not unban all', e.message),
  })

  const columns = [
    {
      key: 'jail',
      header: 'Jail',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.jail}</span>,
    },
    {
      key: 'currently_failed',
      header: 'Currently failed',
      align: 'right',
      sortable: true,
      sortValue: (r) => r.currently_failed ?? 0,
      render: (r) => <span className="tabular-nums text-muted-foreground">{r.currently_failed ?? 0}</span>,
    },
    {
      key: 'currently_banned',
      header: 'Currently banned',
      align: 'right',
      sortable: true,
      sortValue: (r) => r.currently_banned ?? 0,
      render: (r) => (
        <Badge variant={r.currently_banned > 0 ? 'danger' : 'neutral'}>{r.currently_banned ?? 0}</Badge>
      ),
    },
    {
      key: 'total_banned',
      header: 'Total banned',
      align: 'right',
      sortable: true,
      sortValue: (r) => r.total_banned ?? 0,
      render: (r) => <span className="tabular-nums text-muted-foreground">{r.total_banned ?? 0}</span>,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => (
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={() => setDetailJail(r.jail)}>
            <Eye className="h-4 w-4" /> View IPs
          </Button>
          <Button
            variant="danger"
            size="sm"
            disabled={!r.currently_banned}
            onClick={() => setUnbanAllJail(r.jail)}
          >
            <ShieldOff className="h-4 w-4" /> Unban all
          </Button>
        </div>
      ),
    },
  ]

  const eventColumns = [
    {
      key: 'timestamp',
      header: 'Time',
      sortable: true,
      render: (r) => <span className="font-mono text-xs text-muted-foreground">{r.timestamp || '—'}</span>,
    },
    { key: 'jail', header: 'Jail', sortable: true, searchable: true, render: (r) => r.jail || '—' },
    {
      key: 'action',
      header: 'Action',
      sortable: true,
      render: (r) => <Badge variant={r.action === 'ban' ? 'danger' : 'neutral'}>{r.action || '—'}</Badge>,
    },
    {
      key: 'ip',
      header: 'IP',
      searchable: true,
      render: (r) => <span className="font-mono text-sm text-foreground">{r.ip || '—'}</span>,
    },
  ]

  return (
    <div>
      <PageHeader
        title="Fail2ban"
        description="Monitor intrusion-prevention jails and manage banned IP addresses. This list refreshes automatically."
        icon={ShieldAlert}
      >
        <Button variant="secondary" onClick={() => setBootstrapOpen(true)}>
          <Wrench className="h-4 w-4" /> Install / reconfigure jails
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={jails}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search jails…"
        pageSize={15}
        initialSort={{ key: 'currently_banned', dir: 'desc' }}
        getRowKey={(r) => r.jail}
        emptyTitle="No jails active"
        emptyDescription="Install and configure Fail2ban jails to start protecting this server."
        emptyIcon={ShieldAlert}
        emptyAction={
          <Button variant="secondary" onClick={() => setBootstrapOpen(true)}>
            <Wrench className="h-4 w-4" /> Install / reconfigure jails
          </Button>
        }
      />

      <div className="mt-8">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
          <History className="h-4 w-4 text-muted-foreground" /> Recent ban events
        </div>
        <DataTable
          columns={eventColumns}
          data={events.data?.events}
          loading={events.isLoading}
          error={events.error}
          onRetry={events.refetch}
          filterable
          searchPlaceholder="Search events…"
          pageSize={10}
          getRowKey={(r, i) => `${r.timestamp || ''}-${r.ip || ''}-${i}`}
          emptyTitle="No recent events"
          emptyDescription="Ban and unban activity will appear here as it happens."
          emptyIcon={History}
        />
      </div>

      {/* Banned IPs detail dialog */}
      <Dialog open={!!detailJail} onOpenChange={(o) => !o && setDetailJail(null)}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Banned IPs — {detailJail}</DialogTitle>
            <DialogDescription>
              {activeJail?.banned_ips?.length
                ? `${activeJail.banned_ips.length} address${activeJail.banned_ips.length === 1 ? '' : 'es'} currently banned in this jail.`
                : 'No addresses are currently banned in this jail.'}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {activeJail?.banned_ips?.length ? (
              <div className="max-h-80 space-y-2 overflow-y-auto">
                {activeJail.banned_ips.map((ip) => (
                  <div
                    key={ip}
                    className="flex items-center justify-between rounded-btn border border-border px-3 py-2"
                  >
                    <span className="font-mono text-sm text-foreground">{ip}</span>
                    <Button
                      variant="danger"
                      size="sm"
                      loading={unbanMut.isPending && unbanMut.variables?.ip === ip && unbanMut.variables?.jail === activeJail.jail}
                      onClick={() => unbanMut.mutate({ jail: activeJail.jail, ip })}
                    >
                      <Ban className="h-4 w-4" /> Unban
                    </Button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="py-6 text-center text-sm text-muted-foreground">No banned IP addresses.</p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setDetailJail(null)}>Close</Button>
            {activeJail?.banned_ips?.length ? (
              <Button variant="danger" onClick={() => setUnbanAllJail(activeJail.jail)}>
                <ShieldOff className="h-4 w-4" /> Unban all
              </Button>
            ) : null}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Unban-all confirmation */}
      <ConfirmDialog
        open={!!unbanAllJail}
        onOpenChange={(o) => !o && setUnbanAllJail(null)}
        title={unbanAllJail ? `Unban all IPs in ${unbanAllJail}?` : 'Unban all IPs?'}
        description="Every IP currently banned in this jail is released and allowed to connect again. Fail2ban may re-ban offenders on new failures."
        confirmLabel="Unban all"
        loading={unbanAllMut.isPending}
        onConfirm={() => unbanAllJail && unbanAllMut.mutate(unbanAllJail)}
      />

      {/* Bootstrap / reconfigure confirmation */}
      <ConfirmDialog
        open={bootstrapOpen}
        onOpenChange={setBootstrapOpen}
        title="Install / reconfigure jails?"
        description="This installs and reconfigures the standard Fail2ban jails (SSH, panel login, OpenLiteSpeed, Postfix, Dovecot). Existing jail configuration is overwritten."
        confirmLabel="Install / reconfigure"
        variant="primary"
        loading={bootstrapMut.isPending}
        onConfirm={() => bootstrapMut.mutate()}
      />
    </div>
  )
}
