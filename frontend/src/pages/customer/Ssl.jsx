import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ShieldCheck, MoreHorizontal, RefreshCw, Asterisk, ShieldOff, Clock,
} from 'lucide-react'
import { get, post } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { useAuth } from '@/store/auth'
import { formatDateShort } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Badge } from '@/components/ui/Badge'
import { ConfirmDialog, Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter } from '@/components/ui/Dialog'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
} from '@/components/ui/DropdownMenu'
import { toast } from '@/components/ui/Toast'

export default function Ssl() {
  const username = useAccountUsername()
  const admin = useAuth((state) => state.role === 'admin')
  const qc = useQueryClient()
  // confirm = { action: 'issue' | 'wildcard', row }
  const [confirm, setConfirm] = useState(null)
  const [selected, setSelected] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['ssl', admin ? 'all' : username],
    queryFn: () => get(admin ? '/api/v1/admin/ssl' : `/api/v1/accounts/${username}/ssl`),
    enabled: admin || !!username,
  })

  const timerActive = data?.certbot_timer_active

  const issueMut = useMutation({
    mutationFn: (r) => post(admin ? `/api/v1/admin/ssl/domains/${r.domain}/issue` : `/api/v1/accounts/${username}/domains/${r.domain}/ssl/issue`, { force: false }),
    onSuccess: (_res, r) => {
      toast.success('Certificate requested', `SSL issuance started for ${r.domain}.`)
      qc.invalidateQueries({ queryKey: ['ssl'] })
      setConfirm(null)
    },
    onError: (e) => toast.error('Could not issue certificate', e.message),
  })

  const wildcardMut = useMutation({
    mutationFn: (r) => post(admin ? `/api/v1/admin/ssl/domains/${r.domain}/wildcard` : `/api/v1/accounts/${username}/domains/${r.domain}/ssl/wildcard`, { force: false }),
    onSuccess: (_res, r) => {
      toast.success('Wildcard certificate requested', `Wildcard SSL issuance started for *.${r.domain}.`)
      qc.invalidateQueries({ queryKey: ['ssl'] })
      setConfirm(null)
    },
    onError: (e) => toast.error('Could not issue wildcard certificate', e.message),
  })

  const columns = [
    ...(admin ? [{ key: 'username', header: 'Account', sortable: true, searchable: true, render: (r) => <div><div className="font-medium">{r.username}</div>{r.account_status !== 'system' && <div className="text-xs capitalize text-muted-foreground">{r.account_status}</div>}</div> }] : []),
    {
      key: 'domain',
      header: 'Domain',
      sortable: true,
      searchable: true,
      render: (r) => <button type="button" className="font-medium text-accent hover:underline text-left" onClick={() => setSelected(r)} aria-label={`Manage SSL for ${r.domain}`}>{r.domain}</button>,
    },
    {
      key: 'cert_status',
      header: 'Status',
      sortable: true,
      render: (r) => <StatusBadge status={r.cert_status} />,
    },
    {
      key: 'issuer',
      header: 'Issuer',
      searchable: true,
      render: (r) => r.issuer || <span className="text-muted-foreground">—</span>,
    },
    {
      key: 'expiry',
      header: 'Expires',
      sortable: true,
      sortValue: (r) => (typeof r.days_remaining === 'number' ? r.days_remaining : Infinity),
      render: (r) => {
        if (!r.expiry_date) return <span className="text-muted-foreground">—</span>
        const days = r.days_remaining
        const low = typeof days === 'number' && days < 14
        return (
          <span className={low ? 'font-medium text-danger' : 'text-foreground'}>
            {formatDateShort(r.expiry_date)}
            {typeof days === 'number' && (
              <span className={low ? '' : 'text-muted-foreground'}> ({days} days)</span>
            )}
          </span>
        )
      },
    },
    {
      key: 'is_wildcard',
      header: 'Wildcard',
      align: 'right',
      render: (r) => (r.is_wildcard
        ? <Badge variant="accent">*.{r.domain}</Badge>
        : <span className="text-muted-foreground">—</span>),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => {
        const hasCert = r.cert_status && r.cert_status !== 'missing' && r.cert_status !== 'none'
        return (
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setSelected(r)}>Manage SSL</Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={`SSL actions for ${r.domain}`}>
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuItem onSelect={() => setConfirm({ action: 'issue', row: r })}>
                  <RefreshCw className="h-4 w-4" /> {hasCert ? 'Renew now' : 'Issue certificate'}
                </DropdownMenuItem>
                {!r.system && <DropdownMenuItem onSelect={() => setConfirm({ action: 'wildcard', row: r })}>
                  <Asterisk className="h-4 w-4" /> {r.is_wildcard ? 'Renew wildcard' : 'Issue wildcard'}
                </DropdownMenuItem>}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )
      },
    },
  ]

  const isWildcard = confirm?.action === 'wildcard'
  const confirmRow = confirm?.row
  const confirmLoading = isWildcard ? wildcardMut.isPending : issueMut.isPending
  const domains = data?.domains || []
  const secured = domains.filter((row) => row.cert_status && !['missing', 'none', 'expired'].includes(row.cert_status)).length
  const expiring = domains.filter((row) => typeof row.days_remaining === 'number' && row.days_remaining < 30).length
  const unsecured = Math.max(0, domains.length - secured)

  return (
    <div className="ssl-page">
      <PageHeader title={admin ? 'SSL Certificates' : 'SSL/TLS'} description={admin ? "Issue and renew Let's Encrypt certificates across every hosted account." : "Manage Let's Encrypt certificates for your domains."} icon={ShieldCheck}>
        {timerActive === undefined ? null : timerActive ? (
          <Badge variant="success">
            <ShieldCheck className="h-3.5 w-3.5" /> Auto-renew on
          </Badge>
        ) : (
          <Badge variant="warning">
            <ShieldOff className="h-3.5 w-3.5" /> Auto-renew off
          </Badge>
        )}
      </PageHeader>

      <div className="ssl-summary" aria-label="Certificate summary">
        <div><ShieldCheck /><span><strong>{secured}</strong>Secured domains</span></div>
        <div><ShieldOff /><span><strong>{unsecured}</strong>Need a certificate</span></div>
        <div><Clock /><span><strong>{expiring}</strong>Expire within 30 days</span></div>
      </div>

      <div className="ssl-section-title"><div><h2>Certificate status</h2><p>Select a domain to view its certificate or request a free automatic certificate.</p></div><span>Let&apos;s Encrypt · Auto renewal</span></div>

      <DataTable
        columns={columns}
        data={data?.domains}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search domains…"
        pageSize={15}
        initialSort={{ key: 'domain', dir: 'asc' }}
        getRowKey={(r) => r.domain}
        emptyTitle="No domains yet"
        emptyDescription="Add a domain to issue an SSL certificate for it."
        emptyIcon={ShieldCheck}
        className="ssl-domain-table"
      />

      <p className="mt-4 flex items-start gap-2 text-sm text-muted-foreground">
        <Clock className="mt-0.5 h-4 w-4 shrink-0" />
        Wildcard SSL requires this domain's DNS zone to be managed by Boron (DNS-01 challenge);
        issuance is rejected with a clear reason otherwise.
      </p>

      <Dialog open={!!selected} onOpenChange={open => !open && setSelected(null)}>
        <DialogContent size="lg"><DialogHeader><DialogTitle>SSL for {selected?.domain}</DialogTitle><DialogDescription>View certificate details and keep this site protected.</DialogDescription></DialogHeader>
          <DialogBody className="space-y-5"><dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm"><dt className="text-muted-foreground">Status</dt><dd><StatusBadge status={selected?.cert_status}/></dd><dt className="text-muted-foreground">Issuer</dt><dd>{selected?.issuer || 'No certificate issued'}</dd><dt className="text-muted-foreground">Expires</dt><dd>{selected?.expiry_date ? formatDateShort(selected.expiry_date) : '—'}</dd><dt className="text-muted-foreground">Automatic renewal</dt><dd>{timerActive ? 'Enabled' : 'Not active'}</dd></dl>
          <div className="flex flex-wrap gap-3"><Button onClick={() => {setConfirm({action:'issue',row:selected});setSelected(null)}}><RefreshCw className="h-4 w-4"/> {selected?.cert_status && !['missing','none'].includes(selected.cert_status) ? 'Renew certificate' : 'Issue certificate'}</Button>{!selected?.system && <Button variant="outline" onClick={() => {setConfirm({action:'wildcard',row:selected});setSelected(null)}}><Asterisk className="h-4 w-4"/> Wildcard certificate</Button>}</div></DialogBody>
          <DialogFooter><Button variant="secondary" onClick={() => setSelected(null)}>Done</Button></DialogFooter></DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!confirm}
        onOpenChange={(v) => { if (!v) setConfirm(null) }}
        title={isWildcard
          ? (confirmRow?.is_wildcard ? 'Renew wildcard certificate?' : 'Issue wildcard certificate?')
          : (confirmRow && confirmRow.cert_status !== 'missing' && confirmRow.cert_status !== 'none'
            ? 'Renew certificate?'
            : 'Issue certificate?')}
        description={isWildcard
          ? `A wildcard certificate covering *.${confirmRow?.domain || ''} will be requested via Let's Encrypt (DNS-01).`
          : `An SSL certificate for ${confirmRow?.domain || ''} will be requested via Let's Encrypt.`}
        confirmLabel={isWildcard
          ? (confirmRow?.is_wildcard ? 'Renew wildcard' : 'Issue wildcard')
          : (confirmRow && confirmRow.cert_status !== 'missing' && confirmRow.cert_status !== 'none' ? 'Renew now' : 'Issue certificate')}
        variant="primary"
        loading={confirmLoading}
        onConfirm={() => {
          if (!confirmRow) return
          if (isWildcard) wildcardMut.mutate(confirmRow)
          else issueMut.mutate(confirmRow)
        }}
      />
    </div>
  )
}
