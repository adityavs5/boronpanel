import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ShieldHalf, ShieldOff, ShieldAlert, Plus, Trash2, Eye, RotateCcw } from 'lucide-react'
import { get, post, put, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, ConfirmDialog } from '@/components/ui/Dialog'
import { EmptyState, ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { toast } from '@/components/ui/Toast'

const TARGET_OPTIONS = ['ARGS', 'ARGS_NAMES', 'REQUEST_URI', 'QUERY_STRING', 'REQUEST_BODY', 'REQUEST_COOKIES', 'REQUEST_HEADERS:User-Agent', 'REQUEST_HEADERS:Referer']
const EMPTY_RULE = { domain: '', target: 'ARGS', pattern: '' }
const EMPTY_EXCEPTION = { domain: '', rule_id: '', category: '', uri_prefix: '', parameter: '', duration_hours: '24', reason: '' }
const DEFAULT_SETTINGS = { mode: 'disabled', paranoia_level: '1', anomaly_threshold: '5', wp_login_limit: '10', wp_xmlrpc_limit: '5', wp_rate_window_seconds: '60' }

function modeBadge(mode) {
  if (mode === 'protect') return 'success'
  if (mode === 'detect') return 'warning'
  return 'neutral'
}

export default function Waf() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [settingsForm, setSettingsForm] = useState(DEFAULT_SETTINGS)
  const [policy, setPolicy] = useState({ domain: '', mode: 'detect' })
  const [ruleOpen, setRuleOpen] = useState(false)
  const [ruleForm, setRuleForm] = useState(EMPTY_RULE)
  const [deleteRule, setDeleteRule] = useState(null)
  const [exceptionOpen, setExceptionOpen] = useState(false)
  const [exceptionForm, setExceptionForm] = useState(EMPTY_EXCEPTION)
  const [deleteException, setDeleteException] = useState(null)
  const [incident, setIncident] = useState(null)

  const wafQuery = useQuery({ queryKey: ['waf', username], queryFn: () => get('/api/v1/waf') })
  const incidentsQuery = useQuery({ queryKey: ['waf-incidents', username], queryFn: () => get('/api/v1/waf/blocked-requests?limit=100'), refetchInterval: 30000 })
  const status = wafQuery.data

  useEffect(() => {
    if (!status) return
    setSettingsForm({
      mode: status.mode,
      paranoia_level: String(status.paranoia_level),
      anomaly_threshold: String(status.anomaly_threshold),
      wp_login_limit: String(status.wp_login_limit),
      wp_xmlrpc_limit: String(status.wp_xmlrpc_limit),
      wp_rate_window_seconds: String(status.wp_rate_window_seconds),
    })
  }, [status?.mode, status?.paranoia_level, status?.anomaly_threshold, status?.wp_login_limit, status?.wp_xmlrpc_limit, status?.wp_rate_window_seconds])

  const invalidate = () => qc.invalidateQueries({ queryKey: ['waf', username] })
  const settingsMut = useMutation({
    mutationFn: () => put('/api/v1/waf/settings', Object.fromEntries(Object.entries(settingsForm).map(([key, value]) => [key, key === 'mode' ? value : Number(value)]))),
    onSuccess: () => { toast.success('WAF policy updated'); invalidate() },
    onError: (error) => toast.error('Could not update WAF policy', error.message),
  })
  const policyMut = useMutation({
    mutationFn: (body) => put('/api/v1/waf/domain-policy', body),
    onSuccess: () => { toast.success('Domain policy updated'); setPolicy({ domain: '', mode: 'detect' }); invalidate() },
    onError: (error) => toast.error('Could not update domain policy', error.message),
  })
  const ruleMut = useMutation({
    mutationFn: (body) => post('/api/v1/waf/custom-rules', body),
    onSuccess: () => { toast.success('Custom rule added'); setRuleOpen(false); setRuleForm(EMPTY_RULE); invalidate() },
    onError: (error) => toast.error('Could not add rule', error.message),
  })
  const deleteRuleMut = useMutation({
    mutationFn: (id) => del(`/api/v1/waf/custom-rules/${id}`),
    onSuccess: () => { toast.success('Custom rule deleted'); setDeleteRule(null); invalidate() },
    onError: (error) => toast.error('Could not delete rule', error.message),
  })
  const exceptionMut = useMutation({
    mutationFn: (body) => post('/api/v1/waf/exceptions', body),
    onSuccess: () => { toast.success('Temporary exception added'); setExceptionOpen(false); setExceptionForm(EMPTY_EXCEPTION); invalidate() },
    onError: (error) => toast.error('Could not add exception', error.message),
  })
  const deleteExceptionMut = useMutation({
    mutationFn: (id) => del(`/api/v1/waf/exceptions/${id}`),
    onSuccess: () => { toast.success('Exception removed'); setDeleteException(null); invalidate() },
    onError: (error) => toast.error('Could not remove exception', error.message),
  })
  const unblockMut = useMutation({
    mutationFn: (ip) => post('/api/v1/waf/incidents/unblock', { ip }),
    onSuccess: (result) => toast.success(result.unbanned_from?.length ? 'IP unblocked' : 'IP was not temporarily banned', result.unbanned_from?.join(', ')),
    onError: (error) => toast.error('Could not unblock IP', error.message),
  })

  const openIncidentException = (event) => {
    setExceptionForm({ ...EMPTY_EXCEPTION, domain: (event.host || '').split(':')[0], rule_id: event.rule_id ? String(event.rule_id) : '', uri_prefix: (event.path || '').split('?')[0], reason: `False positive from incident ${event.txid}` })
    setIncident(null)
    setExceptionOpen(true)
  }

  if (wafQuery.isLoading) return <CardSkeleton />
  if (wafQuery.error) return <ErrorState error={wafQuery.error} onRetry={wafQuery.refetch} />

  const incidentColumns = [
    { key: 'timestamp', header: 'Time', render: (row) => <span className="whitespace-nowrap text-xs tabular-nums">{row.timestamp || '—'}</span> },
    { key: 'action', header: 'Action', render: (row) => <Badge variant={row.action === 'blocked' ? 'danger' : 'warning'}>{row.action}</Badge> },
    { key: 'host', header: 'Domain', searchable: true },
    { key: 'client_ip', header: 'Client IP', searchable: true, render: (row) => <span className="font-mono text-xs">{row.verified_client_ip || row.client_ip}</span> },
    { key: 'rule_id', header: 'Rule', render: (row) => row.rule_id ? <code>{row.rule_id}</code> : '—' },
    { key: 'message', header: 'Reason', searchable: true },
    { key: 'controls', header: '', align: 'right', render: (row) => <Button size="sm" variant="outline" onClick={() => setIncident(row)}><Eye className="h-4 w-4" /> Inspect</Button> },
  ]

  return (
    <div>
      <PageHeader title="Web application firewall" description="OWASP CRS protection, incidents, and narrow per-site exceptions." icon={ShieldHalf}>
        <Badge variant={modeBadge(status.mode)}>{status.mode === 'protect' ? 'Protecting' : status.mode === 'detect' ? 'Detect only' : 'Disabled'}</Badge>
      </PageHeader>

      {!status.available ? (
        <Card><CardContent><EmptyState icon={ShieldOff} title="ModSecurity is not installed" description="Install the OpenLiteSpeed ModSecurity module and OWASP CRS before enabling protection." /></CardContent></Card>
      ) : (
        <Tabs defaultValue="incidents">
          <TabsList><TabsTrigger value="incidents">Incidents</TabsTrigger><TabsTrigger value="settings">Protection</TabsTrigger><TabsTrigger value="domains">Domain policies</TabsTrigger><TabsTrigger value="exceptions">Exceptions</TabsTrigger><TabsTrigger value="custom">Custom rules</TabsTrigger></TabsList>

          <TabsContent value="incidents"><Card><CardHeader><div><CardTitle>Security incidents</CardTitle><CardDescription>Detected and blocked requests with the concrete CRS rule and verified client address.</CardDescription></div></CardHeader><CardContent><DataTable columns={incidentColumns} data={incidentsQuery.data?.events} loading={incidentsQuery.isLoading} error={incidentsQuery.error} onRetry={incidentsQuery.refetch} getRowKey={(row) => row.txid} filterable searchPlaceholder="Search incidents…" pageSize={20} emptyTitle="No WAF incidents" emptyDescription="Detection events will appear here." emptyIcon={ShieldAlert} /></CardContent></Card></TabsContent>

          <TabsContent value="settings"><Card><CardHeader><div><CardTitle>Protection policy</CardTitle><CardDescription>Start in Detect only, review incidents, then switch to Protect. Higher paranoia finds more unusual traffic and can require exceptions.</CardDescription></div></CardHeader><CardContent><form className="space-y-6" onSubmit={(event) => { event.preventDefault(); settingsMut.mutate() }}>
            <div className="grid gap-5 md:grid-cols-3"><FormField label="Mode"><Select value={settingsForm.mode} onChange={(e) => setSettingsForm((f) => ({ ...f, mode: e.target.value }))}><option value="disabled">Disabled</option><option value="detect">Detect only</option><option value="protect">Protect</option></Select></FormField><FormField label="CRS paranoia level" hint="1 is safest for general hosting."><Select value={settingsForm.paranoia_level} onChange={(e) => setSettingsForm((f) => ({ ...f, paranoia_level: e.target.value }))}>{[1, 2, 3, 4].map((n) => <option key={n} value={n}>Level {n}</option>)}</Select></FormField><FormField label="Anomaly threshold"><Input type="number" min="1" max="100" value={settingsForm.anomaly_threshold} onChange={(e) => setSettingsForm((f) => ({ ...f, anomaly_threshold: e.target.value }))} /></FormField></div>
            <div className="border-t border-border pt-5"><div className="mb-4 font-semibold">WordPress abuse limits</div><div className="grid gap-5 md:grid-cols-3"><FormField label="Login requests"><Input type="number" min="0" max="10000" value={settingsForm.wp_login_limit} onChange={(e) => setSettingsForm((f) => ({ ...f, wp_login_limit: e.target.value }))} /></FormField><FormField label="XML-RPC requests"><Input type="number" min="0" max="10000" value={settingsForm.wp_xmlrpc_limit} onChange={(e) => setSettingsForm((f) => ({ ...f, wp_xmlrpc_limit: e.target.value }))} /></FormField><FormField label="Window (seconds)"><Input type="number" min="10" max="3600" value={settingsForm.wp_rate_window_seconds} onChange={(e) => setSettingsForm((f) => ({ ...f, wp_rate_window_seconds: e.target.value }))} /></FormField></div></div>
            <Button type="submit" loading={settingsMut.isPending}>Save and validate configuration</Button>
          </form></CardContent></Card></TabsContent>

          <TabsContent value="domains"><Card><CardHeader><div><CardTitle>Domain policies</CardTitle><CardDescription>Override the global mode for a real hosted virtual host.</CardDescription></div></CardHeader><CardContent className="space-y-5">
            <form className="grid items-end gap-4 md:grid-cols-[1fr_220px_auto]" onSubmit={(e) => { e.preventDefault(); policyMut.mutate(policy) }}><FormField label="Domain"><Select value={policy.domain} onChange={(e) => setPolicy((f) => ({ ...f, domain: e.target.value }))} required><option value="">Select a hosted domain</option>{status.available_domains.map((domain) => <option key={domain}>{domain}</option>)}</Select></FormField><FormField label="Policy"><Select value={policy.mode} onChange={(e) => setPolicy((f) => ({ ...f, mode: e.target.value }))}><option value="inherit">Use global mode</option><option value="detect">Detect only</option><option value="protect">Protect</option><option value="disabled">Disabled</option></Select></FormField><Button type="submit" loading={policyMut.isPending}>Apply policy</Button></form>
            <DataTable columns={[{ key: 'domain', header: 'Domain', searchable: true }, { key: 'mode', header: 'Mode', render: (row) => <Badge variant={modeBadge(row.mode)}>{row.mode}</Badge> }, { key: 'controls', header: '', align: 'right', render: (row) => <Button size="sm" variant="outline" onClick={() => policyMut.mutate({ domain: row.domain, mode: 'inherit' })}>Use global</Button> }]} data={status.domain_policies} getRowKey={(row) => row.domain} emptyTitle="All domains use the global policy" emptyDescription="Add an override only when a site needs different handling." />
          </CardContent></Card></TabsContent>

          <TabsContent value="exceptions"><Card><CardHeader><div><CardTitle>Temporary exceptions</CardTitle><CardDescription>Exclude one rule or category for one domain. Add a URI or parameter whenever possible.</CardDescription></div><Button onClick={() => setExceptionOpen(true)}><Plus className="h-4 w-4" /> Add exception</Button></CardHeader><CardContent><DataTable columns={[
            { key: 'domain', header: 'Domain', searchable: true }, { key: 'target', header: 'Rule/category', render: (row) => <code>{row.rule_id || row.category}</code> }, { key: 'scope', header: 'Scope', render: (row) => [row.uri_prefix, row.parameter && `parameter: ${row.parameter}`].filter(Boolean).join(' · ') || 'Entire domain' }, { key: 'expires_at', header: 'Expires', render: (row) => <span className="text-xs tabular-nums">{new Date(row.expires_at).toLocaleString()}</span> }, { key: 'active', header: 'State', render: (row) => <Badge variant={row.active ? 'warning' : 'neutral'}>{row.active ? 'Active' : 'Expired'}</Badge> }, { key: 'controls', header: '', align: 'right', render: (row) => <Button size="sm" variant="danger" onClick={() => setDeleteException(row)}><Trash2 className="h-4 w-4" /> Remove</Button> },
          ]} data={status.exceptions} getRowKey={(row) => row.id} filterable searchPlaceholder="Search exceptions…" emptyTitle="No WAF exceptions" emptyDescription="Create exceptions from a reviewed incident." /></CardContent></Card></TabsContent>

          <TabsContent value="custom"><Card><CardHeader><div><CardTitle>Custom blocking rules</CardTitle><CardDescription>Match a validated request field on one hosted domain.</CardDescription></div><Button onClick={() => setRuleOpen(true)}><Plus className="h-4 w-4" /> Add rule</Button></CardHeader><CardContent><DataTable columns={[{ key: 'domain', header: 'Domain', searchable: true }, { key: 'target', header: 'Target', render: (row) => <code>{row.target}</code> }, { key: 'pattern', header: 'Pattern', searchable: true, render: (row) => <code className="text-xs">{row.pattern}</code> }, { key: 'controls', header: '', align: 'right', render: (row) => <Button size="sm" variant="danger" onClick={() => setDeleteRule(row)}><Trash2 className="h-4 w-4" /> Delete</Button> }]} data={status.custom_rules} getRowKey={(row) => row.id} filterable searchPlaceholder="Search custom rules…" emptyTitle="No custom rules" emptyDescription="OWASP CRS remains active without custom rules." /></CardContent></Card></TabsContent>
        </Tabs>
      )}

      <Dialog open={!!incident} onOpenChange={(open) => !open && setIncident(null)}><DialogContent size="lg"><DialogHeader><DialogTitle>WAF incident {incident?.txid}</DialogTitle></DialogHeader><DialogBody className="space-y-4">{incident && <><div className="grid gap-3 rounded-panel border border-border p-4 text-sm sm:grid-cols-2"><div><span className="text-muted-foreground">Domain</span><div className="font-medium">{incident.host}</div></div><div><span className="text-muted-foreground">Verified client IP</span><div className="font-mono">{incident.verified_client_ip}</div></div><div><span className="text-muted-foreground">Request</span><div>{incident.method} {incident.path}</div></div><div><span className="text-muted-foreground">Decision</span><div><Badge variant={incident.action === 'blocked' ? 'danger' : 'warning'}>{incident.action}</Badge></div></div></div><div className="space-y-2">{incident.findings?.map((finding, index) => <div key={`${finding.rule_id}-${index}`} className="rounded-panel border border-border p-3"><div className="font-medium">Rule {finding.rule_id}: {finding.message}</div>{finding.tags?.length > 0 && <div className="mt-1 text-xs text-muted-foreground">{finding.tags.join(' · ')}</div>}</div>)}</div></>}</DialogBody><DialogFooter><Button variant="outline" disabled={!incident} onClick={() => unblockMut.mutate(incident?.verified_client_ip)} loading={unblockMut.isPending}><RotateCcw className="h-4 w-4" /> Unblock temporary ban</Button><Button disabled={!incident} onClick={() => incident && openIncidentException(incident)}>Add narrow exception</Button></DialogFooter></DialogContent></Dialog>

      <Dialog open={exceptionOpen} onOpenChange={setExceptionOpen}><DialogContent size="md"><DialogHeader><DialogTitle>Add temporary WAF exception</DialogTitle></DialogHeader><form onSubmit={(e) => { e.preventDefault(); exceptionMut.mutate({ ...exceptionForm, rule_id: exceptionForm.rule_id ? Number(exceptionForm.rule_id) : null, category: exceptionForm.category || null, duration_hours: Number(exceptionForm.duration_hours) }) }}><DialogBody className="grid gap-4 sm:grid-cols-2"><FormField label="Domain" required><Select value={exceptionForm.domain} onChange={(e) => setExceptionForm((f) => ({ ...f, domain: e.target.value }))} required><option value="">Select domain</option>{status?.available_domains?.map((domain) => <option key={domain}>{domain}</option>)}</Select></FormField><FormField label="Duration"><Select value={exceptionForm.duration_hours} onChange={(e) => setExceptionForm((f) => ({ ...f, duration_hours: e.target.value }))}><option value="1">1 hour</option><option value="6">6 hours</option><option value="24">24 hours</option><option value="168">7 days</option><option value="720">30 days</option></Select></FormField><FormField label="Rule ID" hint="Use either rule ID or category."><Input type="number" min="1" value={exceptionForm.rule_id} onChange={(e) => setExceptionForm((f) => ({ ...f, rule_id: e.target.value, category: e.target.value ? '' : f.category }))} /></FormField><FormField label="Category/tag"><Input value={exceptionForm.category} disabled={!!exceptionForm.rule_id} onChange={(e) => setExceptionForm((f) => ({ ...f, category: e.target.value }))} placeholder="attack-sqli" /></FormField><FormField label="URI prefix" hint="Optional, recommended."><Input value={exceptionForm.uri_prefix} onChange={(e) => setExceptionForm((f) => ({ ...f, uri_prefix: e.target.value }))} placeholder="/wp-admin/" /></FormField><FormField label="Parameter" hint="Optional request parameter."><Input value={exceptionForm.parameter} onChange={(e) => setExceptionForm((f) => ({ ...f, parameter: e.target.value }))} placeholder="content" /></FormField><FormField label="Reason" className="sm:col-span-2"><Input value={exceptionForm.reason} onChange={(e) => setExceptionForm((f) => ({ ...f, reason: e.target.value }))} /></FormField></DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setExceptionOpen(false)}>Cancel</Button><Button type="submit" loading={exceptionMut.isPending}>Add exception</Button></DialogFooter></form></DialogContent></Dialog>

      <Dialog open={ruleOpen} onOpenChange={setRuleOpen}><DialogContent size="sm"><DialogHeader><DialogTitle>Add custom blocking rule</DialogTitle></DialogHeader><form onSubmit={(e) => { e.preventDefault(); ruleMut.mutate(ruleForm) }}><DialogBody className="space-y-4"><FormField label="Domain" required><Select value={ruleForm.domain} onChange={(e) => setRuleForm((f) => ({ ...f, domain: e.target.value }))} required><option value="">Select domain</option>{status?.available_domains?.map((domain) => <option key={domain}>{domain}</option>)}</Select></FormField><FormField label="Target"><Select value={ruleForm.target} onChange={(e) => setRuleForm((f) => ({ ...f, target: e.target.value }))}>{TARGET_OPTIONS.map((target) => <option key={target}>{target}</option>)}</Select></FormField><FormField label="Regex pattern"><Input value={ruleForm.pattern} onChange={(e) => setRuleForm((f) => ({ ...f, pattern: e.target.value }))} maxLength={300} required /></FormField></DialogBody><DialogFooter><Button type="button" variant="secondary" onClick={() => setRuleOpen(false)}>Cancel</Button><Button type="submit" loading={ruleMut.isPending}>Add rule</Button></DialogFooter></form></DialogContent></Dialog>

      <ConfirmDialog open={!!deleteRule} onOpenChange={(open) => !open && setDeleteRule(null)} title="Delete custom rule?" description={deleteRule ? `Delete the ${deleteRule.target} rule for ${deleteRule.domain}?` : ''} confirmLabel="Delete rule" loading={deleteRuleMut.isPending} onConfirm={() => deleteRuleMut.mutate(deleteRule.id)} />
      <ConfirmDialog open={!!deleteException} onOpenChange={(open) => !open && setDeleteException(null)} title="Remove WAF exception?" description={deleteException ? `Protection for ${deleteException.domain} will immediately use rule ${deleteException.rule_id || deleteException.category} again.` : ''} confirmLabel="Remove exception" loading={deleteExceptionMut.isPending} onConfirm={() => deleteExceptionMut.mutate(deleteException.id)} />
    </div>
  )
}
