import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Cpu, Skull, RefreshCw } from 'lucide-react'
import { get, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatBytes } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

function fmtRuntime(seconds) {
  const s = seconds % 60
  const m = Math.floor(seconds / 60) % 60
  const h = Math.floor(seconds / 3600)
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`
}

// Phase 8 feature 10: live process list for the account's uid, auto-refresh 10s.
// Used both as a customer page and (via useAccountUsername) an admin tab.
export default function Processes({ embedded = false }) {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [toKill, setToKill] = useState(null)

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['processes', username],
    queryFn: () => get(`/api/v1/accounts/${username}/processes`),
    enabled: !!username,
    refetchInterval: 10000, // auto-refresh every 10s
  })

  const killMut = useMutation({
    mutationFn: (pid) => del(`/api/v1/accounts/${username}/processes/${pid}`),
    onSuccess: (_r, pid) => { toast.success('Process killed', `PID ${pid}`); setToKill(null); qc.invalidateQueries({ queryKey: ['processes', username] }) },
    onError: (e) => { toast.error('Could not kill process', e.message); setToKill(null) },
  })

  const columns = [
    { key: 'pid', header: 'PID', sortable: true, render: (r) => <span className="font-mono text-xs">{r.pid}</span> },
    { key: 'command', header: 'Command', searchable: true, render: (r) => <span className="break-all font-mono text-xs">{r.command}</span> },
    { key: 'cpu_pct', header: 'CPU %', align: 'right', sortable: true, sortValue: (r) => r.cpu_pct, render: (r) => `${r.cpu_pct}%` },
    { key: 'memory_bytes', header: 'Memory', align: 'right', sortable: true, sortValue: (r) => r.memory_bytes, render: (r) => formatBytes(r.memory_bytes) },
    { key: 'runtime_seconds', header: 'Runtime', align: 'right', sortable: true, sortValue: (r) => r.runtime_seconds, render: (r) => fmtRuntime(r.runtime_seconds) },
    {
      key: 'actions', header: '', align: 'right', render: (r) => (
        <Button variant="ghost" size="icon-sm" title="Kill" onClick={() => setToKill(r)}>
          <Skull className="h-4 w-4 text-danger" />
        </Button>
      ),
    },
  ]

  const table = (
    <>
      <DataTable
        columns={columns}
        data={data?.processes}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search processes…"
        pageSize={25}
        initialSort={{ key: 'cpu_pct', dir: 'desc' }}
        getRowKey={(r) => r.pid}
        emptyTitle="No running processes"
        emptyDescription="This account has no live processes right now."
        emptyIcon={Cpu}
      />
      <ConfirmDialog
        open={!!toKill}
        onOpenChange={(o) => !o && setToKill(null)}
        title={toKill ? `Kill PID ${toKill.pid}?` : ''}
        description={toKill ? toKill.command : ''}
        confirmLabel="Kill process"
        variant="danger"
        loading={killMut.isPending}
        onConfirm={() => killMut.mutate(toKill.pid)}
      />
    </>
  )

  if (embedded) return table

  return (
    <div>
      <PageHeader title="Processes" description="Live processes running under your account. Auto-refreshes every 10 seconds." icon={Cpu}>
        <Button variant="secondary" onClick={() => refetch()} loading={isFetching}><RefreshCw className="h-4 w-4" /> Refresh</Button>
      </PageHeader>
      {table}
    </div>
  )
}
