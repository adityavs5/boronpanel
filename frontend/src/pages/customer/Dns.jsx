import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Network, Plus, Pencil, Trash2, Cloud, CheckCircle2, AlertTriangle, Wand2, FileCode2, Server, Copy } from 'lucide-react'
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
import { Checkbox } from '@/components/ui/Toggle'
import { LongValue } from '@/components/ui/LongValue'
import { dnsOwnerPreview } from '@/lib/dns-owner'
import { InlineError, InlineWarning } from './InlineFeedback'

const DNS_TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SRV', 'CAA']
const linesToList = (text) => (text || '').split('\n').map((s) => s.trim()).filter(Boolean)
// blog.example.com inside the example.com zone is the record name "blog".
const hostedLabel = (name, parent) => name.endsWith(`.${parent}`) ? name.slice(0, -(parent.length + 1)) : name

// The zone's own NS records belong to the Nameservers section of the domain
// page, which also maintains the glue in-zone nameservers need; the record
// API refuses them (400) from both editors.
function NameserversLink({ to, children = 'Nameservers page' }) {
  return <Link to={{ pathname: to, hash: '#nameservers' }} className="font-medium text-link underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/30 rounded-sm">{children}</Link>
}

// --- Mail readiness ---------------------------------------------------------
// GET .../dns-readiness compares the mail records (MX, SPF, DKIM, DMARC) with
// what they should be. When public DNS does not delegate the domain to this
// server (authoritative false), the rows are what public DNS returns: they
// must be changed where the domain's DNS is hosted, so only this server's
// own zone can be repaired here.
function copyValue(value) {
  navigator.clipboard?.writeText(value).then(() => toast.success('Copied'), () => toast.error('Could not copy', 'Select and copy the value manually.'))
}

function ReadinessStatus({ record, publicRow, onReplace }) {
  if (record.status === 'present') return <Badge variant="success"><CheckCircle2 className="h-3 w-3" /> Ready</Badge>
  if (record.status === 'conflicting') return <><Badge variant="warning"><AlertTriangle className="h-3 w-3" /> Conflict</Badge>{!publicRow && <Button size="sm" variant="secondary" onClick={onReplace}>Replace</Button>}</>
  if (record.status === 'optional') return <Badge variant="neutral">Optional</Badge>
  if (record.status === 'inactive') return <Badge variant="warning"><AlertTriangle className="h-3 w-3" /> Activate</Badge>
  if (record.status === 'unknown') return <Badge variant="neutral">Not checked</Badge>
  return <Badge variant="warning">Missing</Badge>
}

function ReadinessRows({ records, zone, publicRows, managed, onRepair }) {
  return (
    <ul className="divide-y divide-border rounded-md border border-border">
      {records.map(record => {
        const fqdn = record.fqdn || (record.name === '@' ? zone : `${record.name}.${zone}`)
        const expected = record.status !== 'present' ? (record.expected || []) : []
        return (
          <li key={`${record.key}-${fqdn}`} className="flex flex-wrap items-start gap-3 px-3 py-2 text-sm">
            <span className="w-16 shrink-0 font-medium uppercase text-foreground">{record.key}</span>
            <span className="min-w-0 flex-1 space-y-1">
              <span className="block break-all font-mono text-xs text-muted-foreground">{fqdn} · {record.type}</span>
              {record.detail && <span className="block break-words text-xs text-foreground">{record.detail}</span>}
              {expected.map(value => (
                <span key={value} className="flex items-start gap-2 text-xs">
                  <code className="min-w-0 flex-1 break-all rounded-sm bg-muted px-1.5 py-0.5 font-mono text-foreground">{value}</code>
                  <Button size="icon-sm" variant="ghost" title={`Copy the expected ${record.type} value for ${fqdn}`} onClick={() => copyValue(value)}><Copy className="h-3.5 w-3.5" /></Button>
                </span>
              ))}
              {record.key === 'dmarc' && record.status === 'optional' && <span className="block text-xs text-muted-foreground">No DMARC record at this name. When email is set up, Boron publishes a monitoring policy (p=none) unless this domain or a parent domain already has a DMARC policy, or public DNS could not be checked. A parent domain's policy also applies here. {managed && !publicRows && <button type="button" className="font-medium text-link underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/30 rounded-sm" onClick={() => document.getElementById('dmarc-policy')?.focus()}>Set a DMARC policy</button>}</span>}
            </span>
            <span className="flex flex-wrap items-center gap-2"><ReadinessStatus record={record} publicRow={publicRows} onReplace={() => onRepair([record.key])} /></span>
          </li>
        )
      })}
    </ul>
  )
}

function MailReadinessCard({ data, zone, repairing, onRepair }) {
  const publicSource = data.source === 'public'
  return (
    <div className="min-w-0 rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold text-foreground">Mail readiness</h2>
          {data.mail_hostname && <p className="text-sm text-muted-foreground">MX uses <span className="break-all font-mono">{data.mail_hostname}</span>. Existing conflicting records are preserved.</p>}
        </div>
        {data.managed && <Button size="sm" variant="secondary" className="h-auto min-h-[var(--control-h-sm)] whitespace-normal py-1 text-left" loading={repairing} onClick={() => onRepair([])}><Wand2 className="h-4 w-4" /> {publicSource ? "Add missing records to this server's zone" : 'Add missing records'}</Button>}
      </div>
      {data.detail && data.authoritative === false && <InlineWarning className="mb-3"><p>{data.detail}</p></InlineWarning>}
      {data.detail && data.authoritative == null && <p className="mb-3 text-sm text-muted-foreground">{data.detail}</p>}
      <ReadinessRows records={data.records || []} zone={zone} publicRows={publicSource} managed={data.managed} onRepair={onRepair} />
      {publicSource && (data.local_records || []).length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer rounded-sm font-medium text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/30">Records in this server's zone</summary>
          <div className="mt-2"><ReadinessRows records={data.local_records} zone={zone} publicRows={false} managed={data.managed} onRepair={onRepair} /></div>
        </details>
      )}
    </div>
  )
}

export default function Dns({ emailOnly = false }) {
  const username = useAccountUsername()
  const { username: scopedUsername } = useParams()
  const qc = useQueryClient()
  const [dialog, setDialog] = useState(null) // {mode, subdomain, type, ttl, values}
  const [toDelete, setToDelete] = useState(null)
  // Values already stored under a name that are not valid; adding another
  // value keeps them as they are and the response lists them.
  const [invalidExisting, setInvalidExisting] = useState(null)
  const [advanced, setAdvanced] = useState(null)
  const [template, setTemplate] = useState('hosting')
  const [dmarc, setDmarc] = useState({ policy: 'none', subdomain_policy: 'none', rua: '' })
  // Set when the customer jumps from a hosted name without its own zone to
  // the zone that holds its records: the table is filtered to that name and
  // "Add record" starts with it.
  const [nameFilter, setNameFilter] = useState('')
  const [prefillName, setPrefillName] = useState('')
  const domainPage = name => scopedUsername ? `/accounts/${username}/domains/${name}` : `/domains/${name}`

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
  // A domain whose zone is not on this server still gets its public records
  // checked (managed false, source public).
  const readinessShown = !!readiness.data && (readiness.data.managed || (readiness.data.records || []).length > 0)
  const ownerPreview = dnsOwnerPreview(zone, dialog?.subdomain)
  const apexName = !ownerPreview.error && ownerPreview.fqdn === zone
  const apexNs = apexName && dialog?.type === 'NS'
  // NS stays listed only while it is already chosen, so the notice below can
  // explain why the apex refuses it instead of the picker silently changing.
  const recordTypes = (emailOnly ? ['MX', 'TXT', 'A', 'CNAME'] : DNS_TYPES).filter((t) => !(apexName && t === 'NS' && !apexNs))
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
  const isApexNsRow = (row) => row.type === 'NS' && row.subdomain === '@'

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
    onSuccess: (result, form) => {
      const invalid = result?.invalid_existing_values || []
      if (invalid.length) setInvalidExisting({ name: form.subdomain?.trim() || '@', type: form.type, items: invalid })
      else { setInvalidExisting(null); toast.success('DNS record saved') }
      invalidate(); setDialog(null)
    },
    // The server's message names the field and the allowed range; it also
    // stays in the dialog (below the values) until the next attempt.
    onError: (e) => toast.error('Could not save record', e.message),
  })
  const openRecordDialog = (next) => { saveMut.reset(); setDialog(next) }
  const editRow = (r) => openRecordDialog({ mode: 'edit', subdomain: r.subdomain, type: r.type, ttl: String(r.ttl), values: r.values.join('\n') })

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
    onSuccess: data => { clearAdvancedErrors(); setAdvanced({ ...data, confirmation: '', changes: null, conflicts: [] }) },
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
  // Errors name the record and type ("mx.example.com MX: ..."); the latest
  // one stays in the editor in full, since it can be longer than a toast.
  function clearAdvancedErrors() { advancedPreview.reset(); templateLoad.reset(); advancedSave.reset() }
  const advancedError = advancedSave.error || advancedPreview.error || templateLoad.error

  const columns = [
    { key: 'subdomain', header: 'Name', sortable: true, searchable: true, render: (r) => isApexNsRow(r) ? <span className="font-mono">{r.subdomain}</span> : <button type="button" className="font-mono text-accent hover:underline text-left" onClick={() => editRow(r)} aria-label={`Edit ${r.type} record ${r.subdomain}`}>{r.subdomain}</button> },
    { key: 'type', header: 'Type', sortable: true, render: (r) => <span className="font-mono text-xs">{r.type}</span> },
    { key: 'ttl', header: 'TTL', sortable: true, render: (r) => r.ttl },
    { key: 'values', header: 'Value(s)', render: (r) => <LongValue value={r.values.join('\n')} label={`${r.type} record value`} /> },
    {
      key: 'actions', header: '', align: 'right', render: (r) => isApexNsRow(r) ? (
        <Button asChild variant="ghost" size="sm"><Link to={{ pathname: domainPage(zone), hash: '#nameservers' }} aria-label={`Manage the nameservers of ${zone}`}><Server className="h-4 w-4" /> Nameservers</Link></Button>
      ) : (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="icon-sm" title="Edit" onClick={() => editRow(r)}>
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
            <div className="flex flex-wrap items-end gap-3"><FormField label="DNS template" hint="Adds missing defaults and keeps existing values. Loading replaces unsaved editor text."><Select value={template} onChange={e => setTemplate(e.target.value)}>{advanced.templates.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</Select></FormField><Button variant="secondary" loading={templateLoad.isPending} disabled={advancedSave.isPending} onClick={() => { clearAdvancedErrors(); templateLoad.mutate() }}>Load template</Button></div>
            {advanced.conflicts?.length > 0 && <div className="text-sm text-muted-foreground" role="status">Existing records preserved: {advanced.conflicts.map(item => `${item.name} (${item.type})`).join(', ')}. Review any intended replacements manually.</div>}
            <FormField label="Editable zone records"><Textarea rows={16} value={advanced.text} disabled={advancedSave.isPending} onChange={e => setAdvanced(current => ({ ...current, text: e.target.value, changes: null, confirmation: '' }))} /></FormField>
            {advanced.changes && <div className="space-y-2"><p className="font-semibold">{advanced.changes.length} record sets will change</p><div className="max-h-48 overflow-auto">{advanced.changes.map(item => <div key={`${item.name}-${item.type}`} className="border-b border-border py-2 text-sm"><strong>{item.action} · {item.name} · {item.type}</strong><div className="break-all font-mono text-xs">Before: {item.before.join(' / ') || 'None'}</div><div className="break-all font-mono text-xs">After: {item.after.join(' / ') || 'Removed'}</div></div>)}</div><FormField label="Confirm zone name" hint={`Type ${advanced.zone} to apply this preview.`}><Input value={advanced.confirmation} onChange={e => setAdvanced(current => ({ ...current, confirmation: e.target.value }))} autoComplete="off" /></FormField></div>}
            <InlineError error={advancedError} className="whitespace-pre-line" />
          </div>}</DialogBody>
          <DialogFooter><Button variant="secondary" disabled={advancedSave.isPending} onClick={() => setAdvanced(null)}>Cancel</Button><Button variant="secondary" disabled={!advanced || advancedSave.isPending || templateLoad.isPending} loading={advancedPreview.isPending} onClick={() => { clearAdvancedErrors(); advancedPreview.mutate() }}>Preview changes</Button><Button disabled={!advanced?.changes?.length || advanced.reviewedText !== advanced.text || advanced.confirmation !== advanced.zone || advancedPreview.isPending || templateLoad.isPending} loading={advancedSave.isPending} onClick={() => { clearAdvancedErrors(); advancedSave.mutate() }}>Apply DNS changes</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <PageHeader title={emailOnly ? 'Email DNS records' : 'DNS'} description={emailOnly ? 'Manage MX, SPF, DKIM, DMARC, webmail, and automatic-client records.' : 'Manage the DNS zone records for your domains.'} icon={Network} primaryAction={<Button
          onClick={() => openRecordDialog({ mode: 'add', subdomain: prefillName || '@', type: emailOnly ? 'MX' : 'A', ttl: '3600', values: '' })}
          disabled={!domain || unmanaged}
        >
          <Plus className="h-4 w-4" /> Add record
        </Button>} />

      <div className="customer-domain-toolbar mb-4 flex flex-wrap items-start gap-3">
        <div className="customer-domain-field">
          <FormField label="Domain" hint="Choose which zone to manage.">
            <Select
              value={domain}
              onChange={(e) => { setNameFilter(''); setPrefillName(''); setDomain(e.target.value) }}
              disabled={domainsQuery.isLoading || !domains.length}
            >
              {!domains.length && <option value="">No domains available</option>}
              {domains.map((d) => (
                <option key={d.domain} value={d.domain}>{d.domain}</option>
              ))}
            </Select>
          </FormField>
        </div>
        {!emailOnly && <Button variant="secondary" disabled={!domain || unmanaged} loading={advancedLoad.isPending} onClick={() => advancedLoad.mutate()}><FileCode2 className="h-4 w-4" /> Zone editor and templates</Button>}
        {/* Provider badge (docs/PLAN-cloudflare.md Phase 1); enable/revert lives in the domain's DNS tab. */}
        {data?.cloudflare ? (
          <Badge variant={data.cloudflare.status === 'active' ? 'accent' : 'warning'}>
            <Cloud className="h-3 w-3" /> Cloudflare{data.cloudflare.status === 'pending' ? ' · pending' : ''}
          </Badge>
        ) : data && !unmanaged ? (
          <Badge variant="neutral">Local DNS</Badge>
        ) : null}
      </div>

      {emailOnly && readinessShown && (
        <div className={readiness.data.managed ? 'mb-5 grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,.8fr)]' : 'mb-5'}>
          <MailReadinessCard data={readiness.data} zone={(readiness.data.zone || domain || '').replace(/\.$/, '')} repairing={repairMut.isPending} onRepair={keys => repairMut.mutate(keys)} />
          {readiness.data.managed && <div className="rounded-lg border border-border bg-card p-4"><h2 className="font-semibold text-foreground">DMARC policy</h2><p className="mb-3 text-sm text-muted-foreground">Start with Monitor, then enforce after reviewing reports.</p><div className="space-y-3"><FormField label="Policy" htmlFor="dmarc-policy"><Select id="dmarc-policy" value={dmarc.policy} onChange={e => setDmarc(value => ({ ...value, policy: e.target.value }))}><option value="none">Monitor (p=none)</option><option value="quarantine">Quarantine</option><option value="reject">Reject</option></Select></FormField><FormField label="Subdomain policy"><Select value={dmarc.subdomain_policy} onChange={e => setDmarc(value => ({ ...value, subdomain_policy: e.target.value }))}><option value="none">Monitor</option><option value="quarantine">Quarantine</option><option value="reject">Reject</option></Select></FormField><FormField label="Aggregate reports" hint={`Blank uses postmaster@${domain}`}><Input type="email" value={dmarc.rua} onChange={e => setDmarc(value => ({ ...value, rua: e.target.value }))} placeholder={`postmaster@${domain}`} /></FormField><Button className="w-full" loading={dmarcMut.isPending} onClick={() => dmarcMut.mutate()}>{dmarc.policy === 'none' ? 'Install monitor policy' : `Install ${dmarc.policy} policy`}</Button>{dmarc.policy !== 'none' && <p className="text-xs text-warning">Enforcement can reject legitimate mail until every sender passes SPF or DKIM alignment.</p>}</div></div>}
        </div>
      )}

      {unmanaged ? (
        <EmptyState
          icon={Network}
          title={data.parent_zone ? 'Managed under a different zone' : 'No DNS zone for this domain'}
          description={
            data.parent_zone ? (
              <>
                <span className="font-mono">{domain}</span> doesn't have its own DNS zone, so records cannot be
                saved here. Its records (and any Cloudflare proxying) live in the{' '}
                <span className="font-medium text-foreground">{data.parent_zone}</span> zone under the name{' '}
                <span className="font-mono">{hostedLabel(domain, data.parent_zone)}</span>
                {domains.some(item => item.domain === data.parent_zone) ? '.' : ', which this account does not manage.'}
              </>
            ) : (
              <>
                <span className="font-mono">{domain}</span> isn't a Boron-managed DNS zone, and no managed zone
                covers it as a subdomain either. DNS (and Cloudflare) is only available for a domain Boron
                manages as its own zone.
              </>
            )
          }
          action={data.parent_zone && domains.some(item => item.domain === data.parent_zone) ? (
            <Button className="h-auto min-h-[var(--control-h)] max-w-full whitespace-normal break-all py-2" onClick={() => { const label = hostedLabel(domain, data.parent_zone); setNameFilter(label); setPrefillName(label); setDomain(data.parent_zone) }}>
              Edit {hostedLabel(domain, data.parent_zone)} in {data.parent_zone}
            </Button>
          ) : null}
        />
      ) : (
        <>
          {invalidExisting && (
            <InlineWarning className="mb-4" title={`${invalidExisting.type} record ${invalidExisting.name}`}>
              <p>Saved. These existing values were kept as they were but are not valid: {invalidExisting.items.map((item, index) => <span key={index}>{index ? '; ' : ''}<span className="break-all font-mono">{item.value}</span> — {item.reason}</span>)}. Edit or remove them.</p>
              <Button size="sm" variant="secondary" onClick={() => setInvalidExisting(null)}>Dismiss</Button>
            </InlineWarning>
          )}
          {prefillName && domain && (
            <div className="settings-notice mb-4 flex flex-wrap items-center justify-between gap-3" role="status">
              <span>Showing the records named <span className="font-mono">{prefillName}</span> in the {zone} zone. New records start with this name.</span>
              <Button size="sm" variant="secondary" onClick={() => { setNameFilter(''); setPrefillName('') }}>Show all records</Button>
            </div>
          )}
          <DataTable
            columns={columns}
            data={rows}
            loading={!!domain && isLoading}
            error={domain ? error : null}
            onRetry={refetch}
            getRowKey={(r) => r._key}
            filterable
            query={nameFilter}
            onQueryChange={setNameFilter}
            pageSize={15}
            searchPlaceholder="Search records…"
            emptyTitle={domain ? (nameFilter ? `No records named ${nameFilter}` : 'No DNS records') : 'Select a domain'}
            emptyDescription={domain ? (nameFilter ? 'Add a record with this name, or clear the search.' : "Add a record to start managing this domain's zone.") : 'Pick a domain above to view its DNS records.'}
            emptyIcon={Network}
          />
        </>
      )}

      <Dialog open={!!dialog} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>{isEdit ? 'Edit DNS record' : 'Add DNS record'}</DialogTitle>
            <DialogDescription>{isEdit ? 'Edit the full value list for this name and type. One value per line.' : 'New values are added to any existing records with the same name and type. Existing values are kept.'}</DialogDescription>
          </DialogHeader>
          {dialog && (
            <form onSubmit={(e) => { e.preventDefault(); if (apexNs || ownerPreview.error || (!isEdit && ownerPreview.confirmRelative && !dialog.relativeConfirmed)) return; saveMut.mutate(dialog) }}>
              <DialogBody className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <FormField label="Name" hint={`@, www, or a full hostname within ${zone}.`} error={ownerPreview.error}>
                    <Input aria-label="DNS record name" value={dialog.subdomain} disabled={isEdit} invalid={!!ownerPreview.error}
                      onChange={(e) => setDialog((d) => ({ ...d, subdomain: e.target.value, relativeConfirmed: false }))} placeholder="@" />
                  </FormField>
                  <FormField label="Type">
                    <Select value={dialog.type} disabled={isEdit}
                      onChange={(e) => setDialog((d) => ({ ...d, type: e.target.value }))}>
                      {recordTypes.map((t) => <option key={t} value={t}>{t}</option>)}
                    </Select>
                  </FormField>
                </div>
                {!ownerPreview.error && <p className="text-sm break-words" role="status">Resulting name: <strong>{ownerPreview.fqdn}</strong></p>}
                {apexNs && <div className="settings-notice" role="status">The zone's own NS records are managed on the <NameserversLink to={domainPage(zone)} />, which also keeps the glue records that in-zone nameservers need. NS records for names below {zone} can be added here.</div>}
                {!isEdit && ownerPreview.confirmRelative && <div className="settings-notice"><p>This is not a full hostname within {zone}. DNS would append this zone to the name.</p><label className="flex items-start gap-2 mt-2"><Checkbox aria-label="Confirm relative DNS name" checked={!!dialog.relativeConfirmed} onCheckedChange={checked => setDialog(current => ({ ...current, relativeConfirmed: checked === true }))}/><span>Use {ownerPreview.fqdn} as a relative name in this zone.</span></label></div>}
                <FormField label="TTL (seconds)">
                  <Input type="number" min="60" value={dialog.ttl}
                    onChange={(e) => setDialog((d) => ({ ...d, ttl: e.target.value }))} />
                </FormField>
                <FormField label="Value(s)" required hint="One value per line. MX: '10 mail.example.com' · TXT: 'v=spf1 -all'">
                  <Textarea required rows={3} value={dialog.values}
                    onChange={(e) => setDialog((d) => ({ ...d, values: e.target.value }))} placeholder="1.2.3.4" />
                </FormField>
                {saveMut.error && <InlineError>{saveMut.error.message}{/nameservers page/i.test(saveMut.error.message) && <> <NameserversLink to={domainPage(zone)}>Open the Nameservers page</NameserversLink></>}</InlineError>}
              </DialogBody>
              <DialogFooter>
                <Button type="button" variant="secondary" onClick={() => setDialog(null)}>Cancel</Button>
                <Button type="submit" disabled={apexNs || !!ownerPreview.error || (!isEdit && ownerPreview.confirmRelative && !dialog.relativeConfirmed)} loading={saveMut.isPending}>Save record</Button>
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
