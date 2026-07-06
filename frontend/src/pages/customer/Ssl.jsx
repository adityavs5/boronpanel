import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ShieldCheck, MoreHorizontal, RefreshCw, Asterisk, ShieldOff, Clock,
} from 'lucide-react'
import { get, post } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatDateShort } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Badge } from '@/components/ui/Badge'
import { ConfirmDialog } from '@/components/ui/Dialog'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
} from '@/components/ui/DropdownMenu'
import { toast } from '@/components/ui/Toast'

export default function Ssl() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  // confirm = { action: 'issue' | 'wildcard', row }
  const [confirm, setConfirm] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['ssl', username],
    queryFn: () => get(`/api/v1/accounts/${username}/ssl`),
    enabled: !!username,
  })

  const timerActive = data?.certbot_timer_active

  const issueMut = useMutation({
    mutationFn: (r) => post(`/api/v1/accounts/${username}/domains/${r.domain}/ssl/issue`, { force: false }),
    onSuccess: (_res, r) => {
      toast.success('Certificate requested', `SSL issuance started for ${r.domain}.`)
      qc.invalidateQueries({ queryKey: ['ssl', username] })
      setConfirm(null)
    },
    onError: (e) => toast.error('Could not issue certificate', e.message),
  })

  const wildcardMut = useMutation({
    mutationFn: (r) => post(`/api/v1/accounts/${username}/domains/${r.domain}/ssl/wildcard`, { force: false }),
    onSuccess: (_res, r) => {
      toast.success('Wildcard certificate requested', `Wildcard SSL issuance started for *.${r.domain}.`)
      qc.invalidateQueries({ queryKey: ['ssl', username] })
      setConfirm(null)
    },
    onError: (e) => toast.error('Could not issue wildcard certificate', e.message),
  })

  const columns = [
    {
      key: 'domain',
      header: 'Domain',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.domain}</span>,
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
          <div className="flex justify-end">
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
                <DropdownMenuItem onSelect={() => setConfirm({ action: 'wildcard', row: r })}>
                  <Asterisk className="h-4 w-4" /> {r.is_wildcard ? 'Renew wildcard' : 'Issue wildcard'}
                </DropdownMenuItem>
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

  return (
    <div>
      <PageHeader title="SSL/TLS" description="Manage Let's Encrypt certificates for your domains." icon={ShieldCheck}>
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
      />

      <p className="mt-4 flex items-start gap-2 text-sm text-muted-foreground">
        <Clock className="mt-0.5 h-4 w-4 shrink-0" />
        Wildcard SSL requires this domain's DNS zone to be managed by Forgehost (DNS-01 challenge);
        issuance is rejected with a clear reason otherwise.
      </p>

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
