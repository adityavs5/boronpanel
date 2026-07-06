import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ShieldHalf, ShieldOff, Plus, Trash2, Globe, Ban } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Switch } from '@/components/ui/Toggle'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import { EmptyState, ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { toast } from '@/components/ui/Toast'

// Mirrors the daemon's ALLOWED_TARGETS allowlist (daemon/waf.py) — `target` is
// interpolated straight into a server-wide OLS config, so only these are valid.
const TARGET_OPTIONS = [
  { value: 'ARGS', label: 'ARGS' },
  { value: 'REQUEST_URI', label: 'REQUEST_URI' },
  { value: 'QUERY_STRING', label: 'QUERY_STRING' },
  { value: 'REQUEST_BODY', label: 'REQUEST_BODY' },
  { value: 'REQUEST_COOKIES', label: 'REQUEST_COOKIES' },
  { value: 'REQUEST_HEADERS:User-Agent', label: 'User-Agent header' },
  { value: 'REQUEST_HEADERS:Referer', label: 'Referer header' },
]

const EMPTY_RULE = { domain: '', target: 'ARGS', pattern: '' }

export default function Waf() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [ruleOpen, setRuleOpen] = useState(false)
  const [ruleForm, setRuleForm] = useState(EMPTY_RULE)
  const [deleteRule, setDeleteRule] = useState(null)
  const [overrideDomain, setOverrideDomain] = useState('')

  const wafQuery = useQuery({
    queryKey: ['waf', username],
    queryFn: () => get('/api/v1/waf'),
  })
  const blockedQuery = useQuery({
    queryKey: ['waf-blocked', username],
    queryFn: () => get('/api/v1/waf/blocked-requests?limit=50'),
  })

  const status = wafQuery.data
  const available = status?.available
  const enabled = !!status?.enabled

  const invalidate = () => qc.invalidateQueries({ queryKey: ['waf', username] })

  const enableMut = useMutation({
    mutationFn: (next) => post('/api/v1/waf/enable', { enabled: next }),
    onSuccess: (_res, next) => {
      toast.success(next ? 'WAF enabled' : 'WAF disabled')
      invalidate()
    },
    onError: (e) => toast.error('Could not change WAF state', e.message),
  })

  const overrideMut = useMutation({
    mutationFn: (body) => post('/api/v1/waf/domain-override', body),
    onSuccess: (_res, body) => {
      toast.success(body.disabled ? 'WAF disabled for domain' : 'WAF re-enabled for domain')
      invalidate()
      setOverrideDomain('')
    },
    onError: (e) => toast.error('Could not update domain override', e.message),
  })

  const addRuleMut = useMutation({
    mutationFn: (body) => post('/api/v1/waf/custom-rules', body),
    onSuccess: () => {
      toast.success('Custom rule added')
      invalidate()
      setRuleOpen(false)
      setRuleForm(EMPTY_RULE)
    },
    onError: (e) => toast.error('Could not add rule', e.message),
  })

  const deleteRuleMut = useMutation({
    mutationFn: (ruleId) => del(`/api/v1/waf/custom-rules/${ruleId}`),
    onSuccess: () => {
      toast.success('Custom rule deleted')
      invalidate()
      setDeleteRule(null)
    },
    onError: (e) => toast.error('Could not delete rule', e.message),
  })

  const overrideColumns = [
    {
      key: 'domain',
      header: 'Domain (WAF disabled)',
      searchable: true,
      sortable: true,
      sortValue: (d) => d,
      searchValue: (d) => d,
      render: (d) => <span className="font-medium text-foreground">{d}</span>,
    },
    {
      key: 'controls',
      header: '',
      align: 'right',
      render: (d) => (
        <Button
          variant="secondary"
          size="sm"
          loading={overrideMut.isPending && overrideMut.variables?.domain === d}
          onClick={() => overrideMut.mutate({ domain: d, disabled: false })}
        >
          Re-enable
        </Button>
      ),
    },
  ]

  const ruleColumns = [
    { key: 'domain', header: 'Domain', sortable: true, searchable: true, render: (r) => <span className="font-medium text-foreground">{r.domain}</span> },
    { key: 'target', header: 'Target', sortable: true, searchable: true, render: (r) => <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{r.target}</code> },
    { key: 'pattern', header: 'Pattern', searchable: true, render: (r) => <code className="text-xs text-muted-foreground">{r.pattern}</code> },
    {
      key: 'controls',
      header: '',
      align: 'right',
      render: (r) => (
        <Button variant="danger" size="sm" onClick={() => setDeleteRule(r)}>
          <Trash2 className="h-4 w-4" /> Delete
        </Button>
      ),
    },
  ]

  const blockedColumns = [
    { key: 'timestamp', header: 'Time', searchable: true, render: (e) => <span className="whitespace-nowrap tabular-nums text-xs">{e.timestamp || '—'}</span> },
    { key: 'client_ip', header: 'Client IP', searchable: true, render: (e) => <span className="tabular-nums">{e.client_ip || '—'}</span> },
    { key: 'host', header: 'Host', searchable: true, render: (e) => e.host || <span className="text-muted-foreground">—</span> },
    { key: 'method', header: 'Method', render: (e) => e.method || '—' },
    { key: 'path', header: 'Path', searchable: true, render: (e) => <span className="break-all">{e.path || '—'}</span> },
    { key: 'status', header: 'Status', align: 'right', render: (e) => <Badge variant="danger">{e.status}</Badge> },
    { key: 'message', header: 'Message', searchable: true, render: (e) => <span className="text-muted-foreground">{e.message || '—'}</span> },
  ]

  return (
    <div>
      <PageHeader
        title="ModSecurity WAF"
        description="Server-wide web application firewall, with per-domain overrides and custom rules."
        icon={ShieldHalf}
      />

      {wafQuery.isLoading ? (
        <CardSkeleton />
      ) : wafQuery.error ? (
        <ErrorState error={wafQuery.error} onRetry={wafQuery.refetch} />
      ) : !available ? (
        <Card>
          <CardContent>
            <EmptyState
              icon={ShieldOff}
              title="ModSecurity is not installed"
              description="The mod_security module is not present on this server, so the WAF cannot be enabled or configured."
            />
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {/* Global on/off */}
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Firewall status</CardTitle>
                <CardDescription>
                  OpenLiteSpeed loads ModSecurity once, server-wide — this is the single global on/off switch. Per-domain control is below.
                </CardDescription>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <Badge variant={enabled ? 'success' : 'neutral'}>{enabled ? 'Enabled' : 'Disabled'}</Badge>
                <Switch
                  checked={enabled}
                  disabled={enableMut.isPending}
                  onCheckedChange={(next) => enableMut.mutate(next)}
                  aria-label="Toggle WAF"
                />
              </div>
            </CardHeader>
          </Card>

          {/* Per-domain overrides */}
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Per-domain overrides</CardTitle>
                <CardDescription>Disable the WAF for a specific domain. Every other domain keeps global protection.</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <form
                className="flex flex-wrap items-end gap-3"
                onSubmit={(e) => {
                  e.preventDefault()
                  overrideMut.mutate({ domain: overrideDomain.trim(), disabled: true })
                }}
              >
                <FormField label="Domain" className="min-w-[220px] flex-1">
                  <Input
                    value={overrideDomain}
                    onChange={(e) => setOverrideDomain(e.target.value)}
                    placeholder="example.com"
                    required
                  />
                </FormField>
                <Button type="submit" loading={overrideMut.isPending && overrideMut.variables?.disabled}>
                  <ShieldOff className="h-4 w-4" /> Disable for domain
                </Button>
              </form>

              <DataTable
                columns={overrideColumns}
                data={status.domain_overrides}
                loading={wafQuery.isLoading}
                error={wafQuery.error}
                onRetry={wafQuery.refetch}
                getRowKey={(d) => d}
                pageSize={10}
                emptyTitle="No per-domain overrides"
                emptyDescription="The WAF applies to every domain."
                emptyIcon={Globe}
              />
            </CardContent>
          </Card>

          {/* Custom rules */}
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Custom rules</CardTitle>
                <CardDescription>Block requests matching a regex pattern on a chosen variable, scoped to one domain.</CardDescription>
              </div>
              <Button className="shrink-0" onClick={() => setRuleOpen(true)}>
                <Plus className="h-4 w-4" /> Add rule
              </Button>
            </CardHeader>
            <CardContent>
              <DataTable
                columns={ruleColumns}
                data={status.custom_rules}
                loading={wafQuery.isLoading}
                error={wafQuery.error}
                onRetry={wafQuery.refetch}
                getRowKey={(r) => r.id}
                filterable
                searchPlaceholder="Search rules…"
                pageSize={10}
                emptyTitle="No custom rules"
                emptyDescription="Add a rule to block requests matching a pattern."
                emptyIcon={ShieldHalf}
                emptyAction={<Button onClick={() => setRuleOpen(true)}><Plus className="h-4 w-4" /> Add rule</Button>}
              />
            </CardContent>
          </Card>

          {/* Blocked requests */}
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Blocked requests</CardTitle>
                <CardDescription>The 50 most recent requests denied by ModSecurity, from the audit log.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <DataTable
                columns={blockedColumns}
                data={blockedQuery.data?.events}
                loading={blockedQuery.isLoading}
                error={blockedQuery.error}
                onRetry={blockedQuery.refetch}
                getRowKey={(e) => e.txid}
                filterable
                searchPlaceholder="Search blocked requests…"
                pageSize={15}
                emptyTitle="No blocked requests"
                emptyDescription="Nothing has been denied by the WAF yet."
                emptyIcon={Ban}
              />
            </CardContent>
          </Card>
        </div>
      )}

      {/* Add custom rule dialog */}
      <Dialog open={ruleOpen} onOpenChange={setRuleOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Add custom rule</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              addRuleMut.mutate({
                domain: ruleForm.domain.trim(),
                target: ruleForm.target,
                pattern: ruleForm.pattern,
              })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Domain" required hint="The rule applies only to requests for this host.">
                <Input
                  autoFocus
                  value={ruleForm.domain}
                  onChange={(e) => setRuleForm((f) => ({ ...f, domain: e.target.value }))}
                  placeholder="example.com"
                  required
                />
              </FormField>
              <FormField label="Target" required hint="Which part of the request to inspect.">
                <Select value={ruleForm.target} onChange={(e) => setRuleForm((f) => ({ ...f, target: e.target.value }))}>
                  {TARGET_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Pattern" required hint="Regex to block. Max 300 chars; no backtick, double-quote, or newline.">
                <Input
                  value={ruleForm.pattern}
                  onChange={(e) => setRuleForm((f) => ({ ...f, pattern: e.target.value }))}
                  placeholder="(?:union\s+select|<script)"
                  maxLength={300}
                  required
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setRuleOpen(false)}>Cancel</Button>
              <Button type="submit" loading={addRuleMut.isPending}>Add rule</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete custom rule confirmation */}
      <ConfirmDialog
        open={!!deleteRule}
        onOpenChange={(o) => !o && setDeleteRule(null)}
        title="Delete custom rule?"
        description={
          deleteRule
            ? `Remove the rule blocking ${deleteRule.target} on ${deleteRule.domain}. This takes effect on the next config reload.`
            : ''
        }
        confirmLabel="Delete rule"
        loading={deleteRuleMut.isPending}
        onConfirm={() => deleteRuleMut.mutate(deleteRule.id)}
      />
    </div>
  )
}
