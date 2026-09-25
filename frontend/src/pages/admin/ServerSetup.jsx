import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Circle, Cloud, Globe2, ServerCog, Settings2 } from 'lucide-react'
import { get, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { toast } from '@/components/ui/Toast'

const selectClass = 'flex h-9 w-full rounded-btn border border-input bg-input-surface px-3 text-sm text-foreground shadow-sm focus-visible:outline-none focus-visible:border-accent focus-visible:ring-[3px] focus-visible:ring-ring/25'
const lines = value => value.split(/[\s,]+/).map(item => item.trim()).filter(Boolean)

function StepShell({ title, description, children }) {
  return <Card><CardHeader><div><CardTitle>{title}</CardTitle><CardDescription>{description}</CardDescription></div></CardHeader><CardContent>{children}</CardContent></Card>
}

export default function ServerSetup() {
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['server-setup'], queryFn: () => get('/api/v1/admin/server-setup') })
  const data = query.data
  const [step, setStep] = useState(1)
  const [form, setForm] = useState({})
  const [preview, setPreview] = useState(null)
  const [replaceConflicts, setReplaceConflicts] = useState(false)
  useEffect(() => {
    if (!data) return
    setStep(current => current === 1 && data.current_step > 1 ? data.current_step : current)
    setForm(current => Object.keys(current).length ? current : {
      panel_hostname: data.system.panel_hostname || '', server_public_ip: data.system.server_public_ip || '',
      contact_email: data.contact_email || '', webmail_hostname: data.system.webmail_hostname || '', pma_hostname: data.system.pma_hostname || '',
      mode: data.draft?.dns_mode || data.dns.mode || 'local', local_nameservers_text: (data.draft?.local_nameservers || data.dns.local_nameservers || []).join('\n'),
      cf_name: 'Primary Cloudflare account', cf_account_id: '', cf_api_token: '', cf_max_zones: 800,
      peer_name: '', peer_type: 'boron', peer_endpoint: '', peer_username: '', peer_credential: '', peer_direction: 'push', peer_zones_text: '',
      maxmind_license_key: '', cert_panel: true, cert_webmail: !!data.system.webmail_hostname, cert_pma: !!data.system.pma_hostname,
    })
  }, [data])
  const update = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const run = useMutation({
    mutationFn: body => post('/api/v1/admin/server-setup/steps', body),
    onSuccess: result => {
      qc.setQueryData(['server-setup'], result.wizard)
      if (result.state === 'preview') setPreview(result)
      else if (result.state === 'waiting_for_external_dns') { setPreview(result); toast.info('Waiting for DNS', 'Retry this step after propagation.') }
      else { setPreview(null); setStep(Math.min(8, result.wizard.current_step)); toast.success(`Step ${result.step} completed`) }
    },
    onError: error => toast.error('Setup step failed', error.message),
  })
  const runStep = body => run.mutate({ step, ...body })
  const resultFor = number => data?.step_results?.[String(number)]
  const bodyForStep = confirm => {
    if (step === 1) return { panel_hostname: form.panel_hostname, server_public_ip: form.server_public_ip, contact_email: form.contact_email }
    if (step === 2) return { mode: form.mode, local_nameservers: form.mode === 'cloudflare' ? [] : lines(form.local_nameservers_text || ''), confirm }
    if (step === 3 && form.mode === 'cloudflare') return { name: form.cf_name, account_id: form.cf_account_id, api_token: form.cf_api_token, max_zones: Number(form.cf_max_zones) }
    if (step === 3 && form.mode === 'cluster') return { name: form.peer_name, peer_type: form.peer_type, endpoint: form.peer_endpoint, username: form.peer_username || null, credential: form.peer_credential || null, direction: form.peer_direction, zones: lines(form.peer_zones_text || ''), verify_tls: true }
    if (step === 3) return {}
    if (step === 4) return { panel_hostname: form.panel_hostname, webmail_hostname: form.webmail_hostname, pma_hostname: form.pma_hostname, server_public_ip: form.server_public_ip, confirm, replace_conflicts: replaceConflicts }
    if (step === 5) return { panel_hostname: form.panel_hostname, webmail_hostname: form.webmail_hostname, pma_hostname: form.pma_hostname, server_public_ip: form.server_public_ip }
    if (step === 6) return { email: form.contact_email, services: [form.cert_panel && 'panel', form.cert_webmail && 'webmail', form.cert_pma && 'phpmyadmin'].filter(Boolean) }
    if (step === 7) return form.maxmind_skip ? { skip: true } : { license_key: form.maxmind_license_key }
    return {}
  }
  const steps = data?.steps || Array.from({ length: 8 }, (_, index) => ({ number: index + 1, title: `Step ${index + 1}` }))

  if (query.isLoading) return <div className="p-6 text-sm text-muted-foreground">Loading server setup…</div>
  if (query.error) return <div className="rounded-card border border-danger/30 bg-danger/5 p-5"><p className="font-semibold">Could not load server setup</p><p className="text-sm text-muted-foreground">{query.error.message}</p><Button className="mt-3" onClick={() => query.refetch()}>Retry</Button></div>

  return <div>
    <PageHeader title="Server Setup" description="A resumable setup for identity, DNS authority, service records, certificates, and GeoLite2." icon={ServerCog}><StatusBadge status={data.completed ? 'completed' : 'pending'} label={data.completed ? 'Setup complete' : `Step ${data.current_step} of 8`} /></PageHeader>
    <div className="mb-5 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{steps.map(item => { const result = resultFor(item.number); const active = item.number === step; return <button type="button" key={item.number} onClick={() => { setStep(item.number); setPreview(null) }} className={`flex items-start gap-3 rounded-btn border p-3 text-left ${active ? 'border-accent bg-accent/5' : 'border-border bg-card hover:border-accent/40'}`}>{result?.state === 'completed' ? <Check className="mt-0.5 h-4 w-4 text-success" /> : <Circle className={`mt-0.5 h-4 w-4 ${active ? 'text-accent' : 'text-muted-foreground'}`} />}<span><span className="block text-xs text-muted-foreground">Step {item.number}</span><span className="block text-sm font-semibold">{item.title}</span>{result?.state && <span className="text-xs capitalize text-muted-foreground">{result.state.replaceAll('_', ' ')}</span>}</span></button> })}</div>

    {step === 1 && <StepShell title="Server identity" description="Set the public identity used by service DNS, certificate requests, and panel links."><div className="grid gap-4 md:grid-cols-2"><FormField label="Panel hostname" required><Input value={form.panel_hostname || ''} onChange={event => update('panel_hostname', event.target.value)} placeholder="panel.example.com" /></FormField><FormField label="Public server IP" required><Input value={form.server_public_ip || ''} onChange={event => update('server_public_ip', event.target.value)} placeholder="192.0.2.10" /></FormField><FormField label="Administrator contact email" required><Input type="email" value={form.contact_email || ''} onChange={event => update('contact_email', event.target.value)} placeholder="admin@example.com" /></FormField></div><Actions run={run} onRun={() => runStep(bodyForStep())} /></StepShell>}
    {step === 2 && <StepShell title="DNS operating mode" description="This becomes the default for new zones. Existing zones remain until you stage them from DNS Setup."><div className="grid gap-3 md:grid-cols-3">{[['cloudflare', Cloud, 'Cloudflare DNS'], ['local', Globe2, 'Local PowerDNS'], ['cluster', Settings2, 'DNS cluster']].map(([id, Icon, label]) => <button type="button" key={id} onClick={() => { update('mode', id); setPreview(null) }} className={`rounded-btn border p-4 text-left ${form.mode === id ? 'border-accent bg-accent/5' : 'border-border'}`}><Icon className="mb-2 h-5 w-5 text-accent" /><strong>{label}</strong></button>)}</div>{form.mode !== 'cloudflare' && <FormField className="mt-4" label="Authoritative nameservers" required><textarea className="min-h-24 w-full rounded-btn border border-input bg-input-surface px-3 py-2 text-sm" value={form.local_nameservers_text || ''} onChange={event => update('local_nameservers_text', event.target.value)} placeholder={'ns1.example.net\nns2.example.net'} /></FormField>}{preview?.step === 2 && <PreviewBox preview={preview} />}<Actions run={run} previewLabel="Preview change" onPreview={() => runStep(bodyForStep(false))} onRun={() => runStep(bodyForStep(true))} disableRun={!preview || (form.mode === 'local' && preview.blockers?.length)} /></StepShell>}
    {step === 3 && <StepShell title="DNS provider" description={form.mode === 'cloudflare' ? 'Connect a scoped Cloudflare API token. It is encrypted and never stored in the wizard.' : form.mode === 'cluster' ? 'Connect the first authoritative peer with an explicit direction and zone boundary.' : 'Confirm the local nameservers configured in the previous step.'}>{form.mode === 'cloudflare' && <div className="grid gap-4 md:grid-cols-2"><FormField label="Display name"><Input value={form.cf_name || ''} onChange={event => update('cf_name', event.target.value)} /></FormField><FormField label="Cloudflare account ID" required><Input autoComplete="off" value={form.cf_account_id || ''} onChange={event => update('cf_account_id', event.target.value)} /></FormField><FormField label="API token" required><Input type="password" autoComplete="new-password" value={form.cf_api_token || ''} onChange={event => update('cf_api_token', event.target.value)} /></FormField><FormField label="Zone capacity"><Input type="number" min="1" value={form.cf_max_zones || 800} onChange={event => update('cf_max_zones', event.target.value)} /></FormField></div>}{form.mode === 'cluster' && <div className="grid gap-4 md:grid-cols-2"><FormField label="Peer name" required><Input value={form.peer_name || ''} onChange={event => update('peer_name', event.target.value)} /></FormField><FormField label="Peer type"><select className={selectClass} value={form.peer_type || 'boron'} onChange={event => update('peer_type', event.target.value)}><option value="boron">Boron</option><option value="directadmin">DirectAdmin</option><option value="cpanel">cPanel / WHM</option></select></FormField><FormField label="HTTPS endpoint" required><Input value={form.peer_endpoint || ''} onChange={event => update('peer_endpoint', event.target.value)} placeholder="https://dns2.example.com" /></FormField>{form.peer_type !== 'boron' && <FormField label="API username" required><Input autoComplete="off" value={form.peer_username || ''} onChange={event => update('peer_username', event.target.value)} /></FormField>}<FormField label="Credential" required={form.peer_type !== 'boron'}><Input type="password" autoComplete="new-password" value={form.peer_credential || ''} onChange={event => update('peer_credential', event.target.value)} /></FormField>{form.peer_type === 'boron' && <FormField label="Direction"><select className={selectClass} value={form.peer_direction || 'push'} onChange={event => update('peer_direction', event.target.value)}><option value="push">Push</option><option value="receive">Receive</option><option value="bidirectional">Bidirectional</option></select></FormField>}<FormField label="Owned zones" hint="Required for receive or bidirectional peers."><textarea className="min-h-20 w-full rounded-btn border border-input bg-input-surface px-3 py-2 text-sm" value={form.peer_zones_text || ''} onChange={event => update('peer_zones_text', event.target.value)} /></FormField></div>}{form.mode === 'local' && <p className="rounded-btn border border-border bg-muted/30 p-4 text-sm">{lines(form.local_nameservers_text || '').join(', ') || 'Return to step 2 and configure nameservers.'}</p>}<Actions run={run} onRun={() => runStep(bodyForStep())} /></StepShell>}
    {step === 4 && <StepShell title="Service hostnames and DNS records" description={`The panel runs on port ${data.system.panel_port}. Its Cloudflare record remains DNS-only because this port is not proxied.`}><div className="grid gap-4 md:grid-cols-3"><FormField label="Panel hostname" required><Input value={form.panel_hostname || ''} onChange={event => update('panel_hostname', event.target.value)} /></FormField><FormField label="Webmail hostname"><Input value={form.webmail_hostname || ''} onChange={event => update('webmail_hostname', event.target.value)} placeholder="webmail.example.com" /></FormField><FormField label="phpMyAdmin hostname"><Input value={form.pma_hostname || ''} onChange={event => update('pma_hostname', event.target.value)} placeholder="phpmyadmin.example.com" /></FormField></div>{preview?.step === 4 && <ServicePreview preview={preview} replace={replaceConflicts} setReplace={setReplaceConflicts} />}<Actions run={run} previewLabel="Preview records" onPreview={() => runStep(bodyForStep(false))} onRun={() => runStep(bodyForStep(true))} disableRun={!preview || (preview.records?.some(row => row.conflicts?.length) && !replaceConflicts)} /></StepShell>}
    {step === 5 && <StepShell title="Verify DNS and delegation" description="This step waits safely while external registrar or DNS changes propagate. It can be retried without changing configuration.">{preview?.step === 5 && <div className="space-y-2">{preview.checks?.map(check => <div key={check.hostname} className="flex items-center justify-between rounded-btn border border-border p-3"><div><strong>{check.hostname}</strong><p className="text-xs text-muted-foreground">Expected {check.expected}; observed {check.observed?.join(', ') || 'nothing'}</p></div><StatusBadge status={check.ready ? 'healthy' : 'pending'} label={check.ready ? 'Ready' : 'Waiting'} /></div>)}</div>}<Actions run={run} runLabel="Verify DNS now" onRun={() => runStep(bodyForStep())} /></StepShell>}
    {step === 6 && <StepShell title="Issue service certificates" description="Boron uses DNS-01 when the selected provider supports it and otherwise uses the dedicated HTTP challenge routes."><div className="space-y-3">{[['cert_panel', 'Panel'], ['cert_webmail', 'Webmail'], ['cert_pma', 'phpMyAdmin']].map(([key, label]) => <label key={key} className="flex items-center gap-3 rounded-btn border border-border p-3"><input type="checkbox" checked={!!form[key]} onChange={event => update(key, event.target.checked)} /><span>{label}</span></label>)}</div><Actions run={run} runLabel="Issue certificates" onRun={() => runStep(bodyForStep())} /></StepShell>}
    {step === 7 && <StepShell title="MaxMind GeoLite2" description="Optional country analytics. The license is used for download and stored only in a root-readable refresh file."><label className="mb-4 flex items-start gap-3"><input type="checkbox" checked={!!form.maxmind_skip} onChange={event => update('maxmind_skip', event.target.checked)} /><span><strong className="block">Skip GeoLite2</strong><span className="text-sm text-muted-foreground">Site statistics continue working without country breakdowns.</span></span></label>{!form.maxmind_skip && <FormField label="MaxMind license key" required><Input type="password" autoComplete="new-password" value={form.maxmind_license_key || ''} onChange={event => update('maxmind_license_key', event.target.value)} /></FormField>}<Actions run={run} runLabel={form.maxmind_skip ? 'Save and skip' : 'Download and configure'} onRun={() => runStep(bodyForStep())} /></StepShell>}
    {step === 8 && <StepShell title="Review and finish" description="Every required step must have a completed result. You can reopen any section later without losing working settings."><div className="space-y-2">{steps.slice(0, 7).map(item => <div key={item.number} className="flex items-center justify-between rounded-btn border border-border p-3"><span>{item.number}. {item.title}</span><StatusBadge status={resultFor(item.number)?.state === 'completed' ? 'completed' : 'pending'} label={resultFor(item.number)?.state || 'Not completed'} /></div>)}</div><Actions run={run} runLabel="Finish server setup" onRun={() => runStep({})} /></StepShell>}
  </div>
}

function Actions({ run, onRun, onPreview, previewLabel, runLabel = 'Save and continue', disableRun = false }) {
  return <div className="mt-5 flex justify-end gap-2">{onPreview && <Button variant="secondary" onClick={onPreview} loading={run.isPending}>{previewLabel}</Button>}<Button onClick={onRun} loading={run.isPending} disabled={disableRun}>{runLabel}</Button></div>
}

function PreviewBox({ preview }) {
  return <div className={`mt-4 rounded-btn border p-4 text-sm ${preview.blockers?.length ? 'border-danger/40 bg-danger/5' : 'border-success/40 bg-success/5'}`}><strong>{preview.current_mode} → {preview.requested_mode}</strong><p className="text-muted-foreground">{preview.message}</p>{preview.blockers?.map(item => <p key={item} className="mt-1 text-danger">{item}</p>)}</div>
}

function ServicePreview({ preview, replace, setReplace }) {
  const conflicts = preview.records?.filter(row => row.conflicts?.length) || []
  return <div className="mt-4 space-y-2">{preview.records?.map(row => <div key={row.hostname} className="flex flex-col justify-between gap-2 rounded-btn border border-border p-3 sm:flex-row sm:items-center"><div><strong>{row.hostname}</strong><p className="text-xs text-muted-foreground">{row.zone ? `${row.type} ${row.value} in ${row.zone}` : 'No Boron-managed parent zone; create this record externally.'}</p></div><StatusBadge status={row.state === 'present' ? 'healthy' : row.state === 'conflict' ? 'failed' : 'pending'} label={row.state} /></div>)}{conflicts.length > 0 && <label className="flex items-start gap-3 rounded-btn border border-danger/40 bg-danger/5 p-3"><input type="checkbox" checked={replace} onChange={event => setReplace(event.target.checked)} /><span><strong className="block">Replace the listed conflicting records</strong><span className="text-xs text-muted-foreground">Unrelated names are retained. Only these exact service hostnames are replaced.</span></span></label>}</div>
}
