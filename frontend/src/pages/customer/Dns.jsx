import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Network, Plus, Pencil, Trash2, Cloud } from 'lucide-react'
import { get, put, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { Badge } from '@/components/ui/Badge'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, Textarea, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'
import { EmptyState } from '@/components/ui/States'

const DNS_TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS']
const linesToList = (text) => (text || '').split('\n').map((s) => s.trim()).filter(Boolean)

export default function Dns() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [domain, setDomain] = useState('')
  const [dialog, setDialog] = useState(null) // {mode, subdomain, type, ttl, values}
  const [toDelete, setToDelete] = useState(null)

  // --- domains for the picker ---------------------------------------------
  const domainsQuery = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })
  const domains = domainsQuery.data?.domains || []

  // Default to the first domain once the list loads.
  useEffect(() => {
    if (!domain && domains.length) setDomain(domains[0].domain)
  }, [domain, domains])

  // --- records for the selected domain ------------------------------------
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['dns-records', domain],
    queryFn: () => get(`/api/v1/dns/zones/${domain}/records`),
    enabled: !!domain,
  })

  const zone = (data?.zone || domain || '').replace(/\.$/, '')
  const rows = (data?.records || []).map((r) => {
    const name = (r.name || '').replace(/\.$/, '')
    let subdomain = '@'
    if (name === zone || !name) subdomain = '@'
    else if (zone && name.endsWith(`.${zone}`)) subdomain = name.slice(0, -(zone.length + 1))
    else subdomain = name
    const values = Array.isArray(r.values)
      ? r.values
      : Array.isArray(r.records)
        ? r.records.map((x) => (typeof x === 'string' ? x : x.content))
        : []
    return { subdomain, type: r.type, ttl: r.ttl, values, _key: `${subdomain}|${r.type}` }
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['dns-records', domain] })

  const saveMut = useMutation({
    mutationFn: (form) =>
      put(`/api/v1/dns/zones/${domain}/records`, {
        domain,
        subdomain: form.subdomain?.trim() || '@',
        type: form.type,
        values: linesToList(form.values),
        ttl: Number(form.ttl) || 3600,
      }),
    onSuccess: () => { toast.success('DNS record saved'); invalidate(); setDialog(null) },
    onError: (e) => toast.error('Could not save record', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (row) => del(`/api/v1/dns/zones/${domain}/records`, { params: { subdomain: row.subdomain, type: row.type } }),
    onSuccess: () => { toast.success('DNS record deleted'); invalidate(); setToDelete(null) },
    onError: (e) => { toast.error('Could not delete record', e.message); setToDelete(null) },
  })

  const columns = [
    { key: 'subdomain', header: 'Name', sortable: true, searchable: true, render: (r) => <span className="font-mono text-xs">{r.subdomain}</span> },
    { key: 'type', header: 'Type', sortable: true, render: (r) => <span className="font-mono text-xs">{r.type}</span> },
    { key: 'ttl', header: 'TTL', sortable: true, render: (r) => r.ttl },
    { key: 'values', header: 'Value(s)', render: (r) => <div className="whitespace-pre-line break-all font-mono text-xs">{r.values.join('\n')}</div> },
    {
      key: 'actions', header: '', align: 'right', render: (r) => (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="icon-sm" title="Edit"
            onClick={() => setDialog({ mode: 'edit', subdomain: r.subdomain, type: r.type, ttl: String(r.ttl), values: r.values.join('\n') })}>
            <Pencil className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon-sm" title="Delete" onClick={() => setToDelete(r)}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ),
    },
  ]

  const isEdit = dialog?.mode === 'edit'
  // A DNS zone (local or Cloudflare) only exists for the exact domain
  // dns.create_zone was called on — never for a subdomain/addon that just
  // lives inside another domain's zone. See docs/PLAN-cloudflare.md.
  const unmanaged = data?.managed === false

  return (
    <div>
      <PageHeader title="DNS" description="Manage the DNS zone records for your domains." icon={Network}>
        <Button
          onClick={() => setDialog({ mode: 'add', subdomain: '@', type: 'A', ttl: '3600', values: '' })}
          disabled={!domain || unmanaged}
        >
          <Plus className="h-4 w-4" /> Add record
        </Button>
      </PageHeader>

      <div className="mb-4 flex items-end gap-3">
        <div className="max-w-sm flex-1">
          <FormField label="Domain" hint="Choose which zone to manage.">
            <Select
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              disabled={domainsQuery.isLoading || !domains.length}
            >
              {!domains.length && <option value="">No domains available</option>}
              {domains.map((d) => (
                <option key={d.domain} value={d.domain}>{d.domain}</option>
              ))}
            </Select>
          </FormField>
        </div>
        {/* Provider badge (docs/PLAN-cloudflare.md Phase 1); enable/revert lives in the domain's DNS tab. */}
        {data?.cloudflare ? (
          <Badge variant={data.cloudflare.status === 'active' ? 'accent' : 'warning'} className="mb-7">
            <Cloud className="h-3 w-3" /> Cloudflare{data.cloudflare.status === 'pending' ? ' · pending' : ''}
          </Badge>
        ) : data && !unmanaged ? (
          <Badge variant="neutral" className="mb-7">Local DNS</Badge>
        ) : null}
      </div>

      {unmanaged ? (
        <EmptyState
          icon={Network}
          title={data.parent_zone ? 'Managed under a different zone' : 'No DNS zone for this domain'}
          description={
            data.parent_zone ? (
              <>
                <span className="font-mono">{domain}</span> doesn't have its own DNS zone — its records (and any
                Cloudflare proxying) live inside <span className="font-medium text-foreground">{data.parent_zone}</span>'s
                zone. Select that domain above to manage records or enable Cloudflare; this subdomain follows
                automatically.
              </>
            ) : (
              <>
                <span className="font-mono">{domain}</span> isn't a Boron-managed DNS zone, and no managed zone
                covers it as a subdomain either. DNS (and Cloudflare) is only available for a domain Boron
                manages as its own zone.
              </>
            )
          }
        />
      ) : (
        <DataTable
          columns={columns}
          data={rows}
          loading={!!domain && isLoading}
          error={domain ? error : null}
          onRetry={refetch}
          getRowKey={(r) => r._key}
          filterable
          pageSize={15}
          searchPlaceholder="Search records…"
          emptyTitle={domain ? 'No DNS records' : 'Select a domain'}
          emptyDescription={domain ? "Add a record to start managing this domain's zone." : 'Pick a domain above to view its DNS records.'}
          emptyIcon={Network}
        />
      )}

      <Dialog open={!!dialog} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>{isEdit ? 'Edit DNS record' : 'Add DNS record'}</DialogTitle>
            <DialogDescription>Saving a name + type replaces its entire value list (one value per line).</DialogDescription>
          </DialogHeader>
          {dialog && (
            <form onSubmit={(e) => { e.preventDefault(); saveMut.mutate(dialog) }}>
              <DialogBody className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <FormField label="Name" hint="@ for the apex, or e.g. www">
                    <Input value={dialog.subdomain} disabled={isEdit}
                      onChange={(e) => setDialog((d) => ({ ...d, subdomain: e.target.value }))} placeholder="@" />
                  </FormField>
                  <FormField label="Type">
                    <Select value={dialog.type} disabled={isEdit}
                      onChange={(e) => setDialog((d) => ({ ...d, type: e.target.value }))}>
                      {DNS_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                    </Select>
                  </FormField>
                </div>
                <FormField label="TTL (seconds)">
                  <Input type="number" min="60" value={dialog.ttl}
                    onChange={(e) => setDialog((d) => ({ ...d, ttl: e.target.value }))} />
                </FormField>
                <FormField label="Value(s)" required hint="One value per line. MX: '10 mail.example.com' · TXT: 'v=spf1 -all'">
                  <Textarea required rows={3} value={dialog.values}
                    onChange={(e) => setDialog((d) => ({ ...d, values: e.target.value }))} placeholder="1.2.3.4" />
                </FormField>
              </DialogBody>
              <DialogFooter>
                <Button type="button" variant="secondary" onClick={() => setDialog(null)}>Cancel</Button>
                <Button type="submit" loading={saveMut.isPending}>Save record</Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={toDelete ? `Delete ${toDelete.type} record at ${toDelete.subdomain}?` : ''}
        description="This removes the entire rrset (all values shown)."
        confirmLabel="Delete record"
        loading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate(toDelete)}
      />
    </div>
  )
}
