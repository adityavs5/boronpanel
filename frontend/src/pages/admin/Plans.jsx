import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Layers, Plus, Pencil, Trash2 } from 'lucide-react'
import { get, del } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

export const PLAN_PRESETS = {
  starter: { label: 'Starter', description: 'Small brochure sites and light email use.', values: { name: 'Starter', cpu_cores: .25, mem_mb: 512, io_mb: 25, pids_max: 50, quota_soft_mb: 2048, quota_hard_mb: 3072, bandwidth_limit_mb: 25600, database_limit: 2, email_account_limit: 5, subdomain_limit: 5, ftp_account_limit: 2, app_limit: 1, redis_enabled: false } },
  wordpress: { label: 'WordPress', description: 'A practical default for managed WordPress sites.', values: { name: 'WordPress', cpu_cores: .5, mem_mb: 1024, io_mb: 50, pids_max: 100, quota_soft_mb: 10240, quota_hard_mb: 12288, bandwidth_limit_mb: 102400, database_limit: 10, email_account_limit: 25, subdomain_limit: 10, ftp_account_limit: 5, app_limit: 3, redis_enabled: true } },
  business: { label: 'Business', description: 'More sites, mailboxes and application capacity.', values: { name: 'Business', cpu_cores: 1, mem_mb: 2048, io_mb: 100, pids_max: 200, quota_soft_mb: 25600, quota_hard_mb: 30720, bandwidth_limit_mb: 256000, database_limit: 25, email_account_limit: 100, subdomain_limit: 50, ftp_account_limit: 10, app_limit: 10, redis_enabled: true } },
  agency: { label: 'Agency', description: 'High-capacity multi-site and application hosting.', values: { name: 'Agency', cpu_cores: 2, mem_mb: 4096, io_mb: 200, pids_max: 300, quota_soft_mb: 51200, quota_hard_mb: 61440, bandwidth_limit_mb: 512000, database_limit: 50, email_account_limit: 250, subdomain_limit: 100, ftp_account_limit: 25, app_limit: 25, redis_enabled: true } },
}

export default function Plans() {
  const qc = useQueryClient()
  const [toDelete, setToDelete] = useState(null)
  const query = useQuery({ queryKey: ['plans'], queryFn: () => get('/api/v1/admin/plans') })
  const remove = useMutation({ mutationFn: plan => del(`/api/v1/admin/plans/${plan.id}`), onSuccess: () => { toast.success('Plan deleted'); setToDelete(null); qc.invalidateQueries({ queryKey: ['plans'] }) }, onError: error => toast.error('Could not delete plan', error.message) })
  const columns = [
    { key: 'name', header: 'Plan', sortable: true, searchable: true, render: row => <span className="font-semibold">{row.name}</span> },
    { key: 'cpu_cores', header: 'CPU', render: row => `${row.cpu_cores ?? row.cpu_pct / 100} cores` },
    { key: 'mem_mb', header: 'RAM', render: row => `${row.mem_mb} MB` },
    { key: 'quota_hard_mb', header: 'Disk', render: row => `${Math.round(row.quota_hard_mb / 1024)} GB` },
    { key: 'bandwidth_limit_mb', header: 'Bandwidth', render: row => row.bandwidth_limit_mb ? `${Math.round(row.bandwidth_limit_mb / 1024)} GB/mo` : 'Unlimited' },
    { key: 'email_account_limit', header: 'Email', render: row => row.email_account_limit ?? 'Unlimited' },
    { key: 'actions', header: '', align: 'right', render: row => <div className="flex justify-end gap-2"><Button asChild size="sm" variant="secondary"><Link to={`/plans/${row.id}/edit`}><Pencil className="h-4 w-4" /> Edit</Link></Button><Button size="icon-sm" variant="ghost" aria-label={`Delete ${row.name}`} onClick={() => setToDelete(row)}><Trash2 className="h-4 w-4 text-danger" /></Button></div> },
  ]
  return <div><PageHeader title="Hosting Plans" description="Create clear resource and feature packages for hosting accounts." icon={Layers}><Button asChild><Link to="/plans/new"><Plus className="h-4 w-4" /> New plan</Link></Button></PageHeader><DataTable columns={columns} data={query.data?.plans} loading={query.isLoading} error={query.error} onRetry={query.refetch} filterable searchPlaceholder="Search plans…" emptyTitle="No plans yet" emptyDescription="Create a package from a ready-to-use template." emptyIcon={Layers} emptyAction={<Button asChild><Link to="/plans/new"><Plus className="h-4 w-4" /> New plan</Link></Button>} /><ConfirmDialog open={!!toDelete} onOpenChange={open => !open && setToDelete(null)} title={`Delete ${toDelete?.name || 'plan'}?`} description="Accounts keep their current limits, but the plan will no longer be available." confirmLabel="Delete plan" variant="danger" loading={remove.isPending} onConfirm={() => remove.mutate(toDelete)} /></div>
}
