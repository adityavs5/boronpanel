import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Network, Plus, Pencil, Trash2, Cloud, CheckCircle2, AlertTriangle, Wand2, FileCode2 } from 'lucide-react'
import { get, post, put, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { useDomainContext } from '@/hooks/useDomainContext'
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

const DNS_TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SRV', 'CAA']
const linesToList = (text) => (text || '').split('\n').map((s) => s.trim()).filter(Boolean)

export default function Dns({ emailOnly = false }) {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [dialog, setDialog] = useState(null) // {mode, subdomain, type, ttl, values}
  const [toDelete, setToDelete] = useState(null)
  const [advanced, setAdvanced] = useState(null)
  const [template, setTemplate] = useState('hosting')
  const [dmarc, setDmarc] = useState({ policy: 'none', subdomain_policy: 'none', rua: '' })

  // --- domains for the picker ---------------------------------------------
  const domainsQuery = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })
  const domains = domainsQuery.data?.domains || []
  const [domain, setDomain] = useDomainContext(username, domains)

  // --- records for the selected domain ------------------------------------
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['dns-records', domain],
    queryFn: () => get(`/api/v1/dns/zones/${domain}/records`),
    enabled: !!domain,
  })
  const readiness = useQuery({
    queryKey: ['mail-dns-readiness', username, domain],
    queryFn: () => get(`/api/v1/accounts/${username}/email/domains/${domain}/dns-readiness`),
    enabled: emailOnly && !!username && !!domain,
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
  }).filter((row) => !emailOnly || row.type === 'MX' || (row.type === 'TXT' && (row.subdomain === '@' || row.subdomain === '_dmarc' || row.subdomain.includes('._domainkey'))) || ['mail', 'webmail', 'autodiscover', 'autoconfig'].includes(row.subdomain))

  const invalidate = () => qc.invalidateQueries({ queryKey: ['dns-records', domain] })

  const saveMut = useMutation({
    mutationFn: (form) =>
      put(`/api/v1/dns/zones/${domain}/records`, {
        domain,
        subdomain: form.subdomain?.trim() || '@',
        type: form.type,
        values: linesToList(form.values),
        ttl: Number(form.ttl) || 3600,
        mode: isEdit ? 'replace' : 'add',
      }),
    onSuccess: () => { toast.success('DNS record saved'); invalidate(); setDialog(null) },
    onError: (e) => toast.error('Could not save record', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (row) => del(`/api/v1/dns/zones/${domain}/records`, { params: { subdomain: row.subdomain, type: row.type } }),
    onSuccess: () => { toast.success('DNS record deleted'); invalidate(); setToDelete(null) },
    onError: (e) => { toast.error('Could not delete record', e.message); setToDelete(null) },
  })
  const repairMut = useMutation({
    mutationFn: (replace_conflicts = []) => post(`/api/v1/accounts/${username}/email/domains/${domain}/dns-repair`, { replace_conflicts }),
    onSuccess: () => { toast.success('Mail DNS records updated'); readiness.refetch(); invalidate() },
    onError: e => toast.error('Could not repair mail DNS', e.message),
  })
  const dmarcMut = useMutation({
    mutationFn: () => put(`/api/v1/accounts/${username}/email/domains/${domain}/dmarc`, dmarc),
    onSuccess: () => { toast.success('DMARC policy installed'); readiness.refetch(); invalidate() },
    onError: e => toast.error('Could not install DMARC', e.message),
  })

  const advancedLoad = useMutation({
    mutationFn: () => get(`/api/v1/dns/zones/${domain}/advanced`),
    onSuccess: data => setAdvanced({ ...data, confirmation: '', changes: null, conflicts: [] }),
    onError: e => toast.error('Could not load zone editor', e.message),
  })
  const advancedPreview = useMutation({
    mutationFn: () => post(`/api/v1/dns/zones/${advanced.zone}/advanced/preview`, { text: advanced.text, fingerprint: advanced.fingerprint }),
    onSuccess: data => setAdvanced(current => ({ ...current, changes: data.changes, reviewedText: current.text })),
    onError: e => toast.error('Could not preview DNS changes', e.message),
  })
  const templateLoad = useMutation({
    mutationFn: () => post(`/api/v1/dns/zones/${advanced.zone}/advanced/template`, { template }),
    onSuccess: data => setAdvanced(current => ({ ...current, ...data, confirmation: '', changes: null })),
    onError: e => toast.error('Could not load DNS template', e.message),
  })
  const advancedSave = useMutation({
    mutationFn: () => put(`/api/v1/dns/zones/${advanced.zone}/advanced`, { text: advanced.text, fingerprint: advanced.fingerprint, confirmation: advanced.confirmation }),
    onSuccess: data => { toast.success('DNS changes applied', `${data.changed} record sets updated. Previous configuration retained for administrator recovery.`); qc.invalidateQueries({ queryKey: ['dns-records', data.zone] }); setAdvanced(null) },
    onError: e => toast.error('Could not apply DNS changes', e.message),
  })

  const columns = [
    { key: 'subdomain', header: 'Name', sortable: true, searchable: true, render: (r) => <button type="button" className="font-mono text-accent hover:underline text-left" onClick={() => setDialog({ mode: 'edit', subdomain: r.subdomain, type: r.type, ttl: String(r.ttl), values: r.values.join('\n') })} aria-label={`Edit ${r.type} record ${r.subdomain}`}>{r.subdomain}</button> },
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
      <Dialog open={!!advanced} onOpenChange={open => { if (!open && !advancedSave.isPending) setAdvanced(null) }}>
        <DialogContent className="max-w-4xl">
          <DialogHeader><DialogTitle>Zone editor · {advanced?.zone}</DialogTitle><DialogDescription>Edit BIND-format records, preview every change, then type the zone name to apply. SOA, apex NS, DNSSEC, disabled records and provider-managed settings are preserved.</DialogDescription></DialogHeader>
          <DialogBody>{advanced && <div className="space-y-4">
            <div className="flex flex-wrap items-end gap-3"><FormField label="DNS template" hint="Adds missing defaults and keeps existing values. Loading replaces unsaved editor text."><Select value={template} onChange={e => setTemplate(e.target.value)}>{advanced.templates.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</Select></FormField><Button variant="secondary" loading={templateLoad.isPending} disabled={advancedSave.isPending} onClick={() => templateLoad.mutate()}>Load template</Button></div>
            {advanced.conflicts?.length > 0 && <div className="text-sm text-muted-foreground" role="status">Existing records preserved: {advanced.conflicts.map(item => `${item.name} (${item.type})`).join(', ')}. Review any intended replacements manually.</div>}
            <FormField label="Editable zone records"><Textarea rows={16} value={advanced.text} disabled={advancedSave.isPending} onChange={e => setAdvanced(current => ({ ...current, text: e.target.value, changes: null, confirmation: '' }))} /></FormField>
            {advanced.changes && <div className="space-y-2"><p className="font-semibold">{advanced.changes.length} record sets will change</p><div className="max-h-48 overflow-auto">{advanced.changes.map(item => <div key={`${item.name}-${item.type}`} className="border-b border-border py-2 text-sm"><strong>{item.action} · {item.name} · {item.type}</strong><div className="break-all font-mono text-xs">Before: {item.before.join(' / ') || 'None'}</div><div className="break-all font-mono text-xs">After: {item.after.join(' / ') || 'Removed'}</div></div>)}</div><FormField label="Confirm zone name" hint={`Type ${advanced.zone} to apply this preview.`}><Input value={advanced.confirmation} onChange={e => setAdvanced(current => ({ ...current, confirmation: e.target.value }))} autoComplete="off" /></FormField></div>}
          </div>}</DialogBody>
          <DialogFooter><Button variant="secondary" disabled={advancedSave.isPending} onClick={() => setAdvanced(null)}>Cancel</Button><Button variant="secondary" disabled={!advanced || advancedSave.isPending || templateLoad.isPending} loading={advancedPreview.isPending} onClick={() => advancedPreview.mutate()}>Preview changes</Button><Button disabled={!advanced?.changes?.length || advanced.reviewedText !== advanced.text || advanced.confirmation !== advanced.zone || advancedPreview.isPending || templateLoad.isPending} loading={advancedSave.isPending} onClick={() => advancedSave.mutate()}>Apply DNS changes</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <PageHeader title={emailOnly ? 'Email DNS Records' : 'DNS'} description={emailOnly ? 'Manage MX, SPF, DKIM, DMARC, webmail, and automatic-client records.' : 'Manage the DNS zone records for your domains.'} icon={Network}>
        <Button
          onClick={() => setDialog({ mode: 'add', subdomain: '@', type: emailOnly ? 'MX' : 'A', ttl: '3600', values: '' })}
          disabled={!domain || unmanaged}
        >
          <Plus className="h-4 w-4" /> Add record
        </Button>
      </PageHeader>

      {!emailOnly && <div className="mb-4 flex flex-wrap gap-2"><Button variant="secondary" size="sm" disabled={!domain || unmanaged} loading={advancedLoad.isPending} onClick={() => advancedLoad.mutate()}><FileCode2 className="h-4 w-4" /> Zone editor and templates</Button></div>}

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

      {emailOnly && readiness.data?.managed && (
        <div className="mb-5 grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,.8fr)]">
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold text-foreground">Mail readiness</h2><p className="text-sm text-muted-foreground">MX uses <span className="font-mono">{readiness.data.mail_hostname}</span>. Existing conflicting records are preserved.</p></div><Button size="sm" variant="secondary" loading={repairMut.isPending} onClick={() => repairMut.mutate([])}><Wand2 className="h-4 w-4" /> Add missing records</Button></div>
            <div className="divide-y divide-border rounded-md border border-border">
              {(readiness.data.records || []).map(record => <div key={record.key} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm"><span className="w-20 font-medium uppercase text-foreground">{record.key}</span><span className="min-w-0 flex-1"><span className="block truncate font-mono text-xs text-muted-foreground">{record.name} · {record.type}</span>{record.detail && <span className="block text-xs text-warning">{record.detail}</span>}</span>{record.status === 'present' ? <Badge variant="success"><CheckCircle2 className="h-3 w-3" /> Ready</Badge> : record.status === 'conflicting' ? <><Badge variant="warning"><AlertTriangle className="h-3 w-3" /> Conflict</Badge><Button size="sm" variant="secondary" onClick={() => repairMut.mutate([record.key])}>Replace</Button></> : record.status === 'optional' ? <Badge variant="neutral">Optional</Badge> : record.status === 'inactive' ? <Badge variant="warning"><AlertTriangle className="h-3 w-3" /> Activate</Badge> : <Badge variant="warning">Missing</Badge>}</div>)}
            </div>
          </div>
          <div className="rounded-lg border border-border bg-card p-4"><h2 className="font-semibold text-foreground">DMARC policy</h2><p className="mb-3 text-sm text-muted-foreground">Start with Monitor, then enforce after reviewing reports.</p><div className="space-y-3"><FormField label="Policy"><Select value={dmarc.policy} onChange={e => setDmarc(value => ({ ...value, policy: e.target.value }))}><option value="none">Monitor (p=none)</option><option value="quarantine">Quarantine</option><option value="reject">Reject</option></Select></FormField><FormField label="Subdomain policy"><Select value={dmarc.subdomain_policy} onChange={e => setDmarc(value => ({ ...value, subdomain_policy: e.target.value }))}><option value="none">Monitor</option><option value="quarantine">Quarantine</option><option value="reject">Reject</option></Select></FormField><FormField label="Aggregate reports" hint={`Blank uses postmaster@${domain}`}><Input type="email" value={dmarc.rua} onChange={e => setDmarc(value => ({ ...value, rua: e.target.value }))} placeholder={`postmaster@${domain}`} /></FormField><Button className="w-full" loading={dmarcMut.isPending} onClick={() => dmarcMut.mutate()}>{dmarc.policy === 'none' ? 'Install monitor policy' : `Install ${dmarc.policy} policy`}</Button>{dmarc.policy !== 'none' && <p className="text-xs text-warning">Enforcement can reject legitimate mail until every sender passes SPF or DKIM alignment.</p>}</div></div>
        </div>
      )}

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
            <DialogDescription>{isEdit ? 'Edit the full value list for this name and type. One value per line.' : 'New values are added to any existing records with the same name and type. Existing values are kept.'}</DialogDescription>
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
                      {(emailOnly ? ['MX', 'TXT', 'A', 'CNAME'] : DNS_TYPES).map((t) => <option key={t} value={t}>{t}</option>)}
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
