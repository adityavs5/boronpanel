import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldCheck, Flame, ShieldHalf, Gauge, ArrowRight, AlertTriangle } from 'lucide-react'
import { get, put } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { ErrorState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'

export default function SecurityCenter() {
  const qc = useQueryClient()
  const query = useQuery({
    queryKey: ['security-center'],
    queryFn: async () => { const [firewall, waf, ols] = await Promise.all([get('/api/v1/firewall/status'), get('/api/v1/waf'), get('/api/v1/admin/openlitespeed')]); return { firewall, waf, ols } },
  })
  const refresh = () => qc.invalidateQueries({ queryKey: ['security-center'] })
  const wafMut = useMutation({ mutationFn: mode => put('/api/v1/waf/settings', { mode, paranoia_level: 1, anomaly_threshold: 5, wp_login_limit: 10, wp_xmlrpc_limit: 5, wp_rate_window_seconds: 60 }), onSuccess: () => { toast.success('WAF protection updated'); refresh() }, onError: e => toast.error('Could not update WAF', e.message) })
  const abuseMut = useMutation({ mutationFn: preset => put('/api/v1/admin/openlitespeed/abuse-controls', { preset }), onSuccess: () => { toast.success('OpenLiteSpeed abuse controls updated'); refresh() }, onError: e => toast.error('Could not update abuse controls', e.message) })
  if (query.isLoading) return <CardSkeleton />
  if (query.error) return <ErrorState error={query.error} onRetry={query.refetch} />
  const { firewall, waf, ols } = query.data
  const throttle = ols.settings?.throttle_preset || 'disabled'
  return <div>
    <PageHeader title="Security Center" description="Firewall, web application protection, and OpenLiteSpeed request controls in one place." icon={ShieldCheck} />
    <div className="grid gap-4 lg:grid-cols-3">
      <Summary icon={Flame} title="Server firewall" status={firewall.active ? 'Active' : 'Disabled'} good={firewall.active} description={`${firewall.rule_count ?? 0} configured firewall rules · WAF configured separately`} to="/firewall" />
      <Summary icon={ShieldHalf} title="Web application firewall" status={waf.mode} good={waf.mode === 'protect'} description={`${waf.exceptions?.filter(item => item.active).length || 0} active exceptions`} to="/waf" />
      <Summary icon={Gauge} title="Application abuse controls" status={throttle} good={throttle !== 'disabled'} description="OpenLiteSpeed per-client connection and request limits" to="/openlitespeed" />
    </div>

    <Card className="mt-5"><CardHeader><CardTitle>Web application protection</CardTitle><CardDescription>Detect logs OWASP CRS findings. Protect blocks requests above the anomaly threshold. Existing domain exceptions remain in place.</CardDescription></CardHeader><CardContent><div className="flex flex-wrap gap-2">{['disabled', 'detect', 'protect'].map(mode => <Button key={mode} variant={waf.mode === mode ? 'primary' : 'secondary'} loading={wafMut.isPending} onClick={() => wafMut.mutate(mode)}>{mode[0].toUpperCase() + mode.slice(1)}</Button>)}</div></CardContent></Card>

    <Card className="mt-5"><CardHeader><CardTitle>OpenLiteSpeed abuse controls</CardTitle><CardDescription>Balanced is a safe starting point for ordinary shared hosting. Strict lowers dynamic request and connection limits. Custom values remain available in OpenLiteSpeed settings.</CardDescription></CardHeader><CardContent><div className="flex flex-wrap gap-2">{['disabled', 'balanced', 'strict'].map(preset => <Button key={preset} variant={throttle === preset ? 'primary' : 'secondary'} loading={abuseMut.isPending} onClick={() => abuseMut.mutate(preset)}>{preset[0].toUpperCase() + preset.slice(1)}</Button>)}</div><div className="mt-4 flex gap-3 rounded-btn border border-warning/40 bg-warning/5 p-3 text-sm"><AlertTriangle className="h-5 w-5 shrink-0 text-warning" /><p>These controls reduce connection and application abuse. They cannot absorb a volumetric attack that saturates the network link; use Cloudflare or upstream provider filtering for that.</p></div></CardContent></Card>
  </div>
}

function Summary({ icon: Icon, title, status, good, description, to }) { return <Card><CardHeader><div className="flex items-start justify-between"><Icon className="h-7 w-7 text-accent" /><Badge variant={good ? 'success' : 'outline'}>{String(status).replaceAll('_', ' ')}</Badge></div><CardTitle>{title}</CardTitle><CardDescription>{description}</CardDescription></CardHeader><CardContent><Button asChild variant="secondary" className="w-full"><Link to={to}>Open settings <ArrowRight className="h-4 w-4" /></Link></Button></CardContent></Card> }
