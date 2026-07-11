import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip as RTooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { Cpu, MemoryStick, HardDrive, Activity, ArrowDownUp, Clock, Cloud, BellRing, Save } from 'lucide-react'
import { get, patch } from '@/lib/api'
import { formatBytes, formatDuration, formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { ProgressBar } from '@/components/ui/Progress'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Switch } from '@/components/ui/Toggle'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { ErrorState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'

function Gauge({ icon: Icon, label, pct, detail }) {
  const p = Math.round(pct ?? 0)
  return (
    <Card>
      <CardContent className="py-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Icon className="h-4 w-4" /> {label}
          </div>
          <span className="text-lg font-semibold tabular-nums text-foreground">{p}%</span>
        </div>
        <ProgressBar value={p} className="mt-3" />
        {detail && <div className="mt-2 text-xs text-muted-foreground">{detail}</div>}
      </CardContent>
    </Card>
  )
}

function ChartCard({ title, data, dataKey, color, formatY }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={`g-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={color} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--border))" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: 'rgb(var(--muted-fg))' }} tickLine={false} axisLine={false} minTickGap={40} />
              <YAxis tick={{ fontSize: 11, fill: 'rgb(var(--muted-fg))' }} tickLine={false} axisLine={false} width={48} tickFormatter={formatY} />
              <RTooltip
                contentStyle={{ background: 'rgb(var(--card))', border: '1px solid rgb(var(--border))', borderRadius: 8, fontSize: 12 }}
                labelStyle={{ color: 'rgb(var(--muted-fg))' }}
                formatter={(v) => [formatY ? formatY(v) : v, title]}
              />
              <Area type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2} fill={`url(#g-${dataKey})`} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  )
}

function CloudflareCard() {
  // Cloudflare DNS/CDN provider health (docs/PLAN-cloudflare.md Phase 0).
  // Polled gently — every probe makes a real outbound API call.
  const cf = useQuery({ queryKey: ['cloudflare-health'], queryFn: () => get('/api/v1/cloudflare/health'), refetchInterval: 60_000 })
  const d = cf.data
  const badge = cf.isLoading
    ? <Badge variant="neutral">Checking…</Badge>
    : cf.isError
      ? <Badge variant="danger">Unavailable</Badge>
      : !d.configured
        ? <Badge variant="neutral">Not configured</Badge>
        : d.ok
          ? <Badge variant="success">Healthy</Badge>
          : <Badge variant="danger">Error</Badge>

  return (
    <Card className="mt-6">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2"><Cloud className="h-4 w-4" /> Cloudflare</CardTitle>
        {badge}
      </CardHeader>
      <CardContent>
        {cf.isError ? (
          <p className="text-sm text-muted-foreground">Could not query the daemon for Cloudflare status.</p>
        ) : cf.isLoading ? null : !d.configured ? (
          <p className="text-sm text-muted-foreground">
            No API token set. Add <code className="font-mono text-xs">CLOUDFLARE_API_TOKEN</code> to secrets.env to
            enable Cloudflare DNS/CDN for customer zones.
          </p>
        ) : (
          <div className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">API token</span>
              <span className="font-medium">{d.token_valid ? 'valid' : 'invalid'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">API reachable</span>
              <span className="font-medium">{d.api_ok ? 'yes' : 'no'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Edge IP ranges</span>
              <span className="font-medium">
                {d.ranges_file?.exists
                  ? `updated ${formatDuration(d.ranges_file.age_seconds)} ago${d.ranges_file.stale ? ' (stale)' : ''}`
                  : 'not fetched yet'}
              </span>
            </div>
            {d.error && <p className="pt-1 text-xs text-danger">{d.error}</p>}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// Run A feature 5: per-service uptime sparkline drawn as inline SVG (the
// series is 0/1 up-down data -- a step-line, not an area chart).
function UptimeSparkline({ series }) {
  if (!series?.length) return <span className="text-xs text-muted-foreground">No checks yet</span>
  const w = 220, hgt = 24
  const step = w / Math.max(series.length - 1, 1)
  const points = series.map(([, up], i) => `${(i * step).toFixed(1)},${up ? 3 : hgt - 3}`).join(' ')
  return (
    <svg width={w} height={hgt} className="shrink-0" aria-hidden>
      <polyline points={points} fill="none" stroke="#1FBED6" strokeWidth="1.5" />
    </svg>
  )
}

function MonitoringCard() {
  const qc = useQueryClient()
  const settings = useQuery({ queryKey: ['monitoring-settings'], queryFn: () => get('/api/v1/admin/monitoring/settings') })
  const history = useQuery({
    queryKey: ['monitoring-history'],
    queryFn: () => get('/api/v1/admin/monitoring/history?hours=24'),
    refetchInterval: 60_000,
  })
  const [form, setForm] = useState(null)
  const active = form ?? {
    admin_email: settings.data?.admin_email || '',
    cooldown_minutes: settings.data?.cooldown_minutes ?? 30,
    enabled: settings.data?.enabled ?? true,
  }

  const saveMut = useMutation({
    mutationFn: (body) => patch('/api/v1/admin/monitoring/settings', body),
    onSuccess: () => {
      toast.success('Monitoring settings saved')
      qc.invalidateQueries({ queryKey: ['monitoring-settings'] })
      setForm(null)
    },
    onError: (e) => toast.error('Could not save settings', e.message),
  })

  const services = history.data?.services || []

  return (
    <Card className="mt-6">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2"><BellRing className="h-4 w-4" /> Service monitoring</CardTitle>
          <CardDescription>
            Checked every 5 minutes. The admin address gets an email when a service goes down and when it recovers
            (re-alerts throttled per service).
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* Settings row */}
        <div className="flex flex-wrap items-end gap-3">
          <FormField label="Alert email" className="min-w-56 flex-1">
            <Input
              type="email"
              placeholder="admin@example.com"
              value={active.admin_email}
              onChange={(e) => setForm({ ...active, admin_email: e.target.value })}
            />
          </FormField>
          <FormField label="Cooldown (min)" className="w-32">
            <Input
              type="number" min="1" max="1440"
              value={active.cooldown_minutes}
              onChange={(e) => setForm({ ...active, cooldown_minutes: e.target.value })}
            />
          </FormField>
          <div className="flex h-9 items-center gap-2">
            <Switch checked={active.enabled} onCheckedChange={(v) => setForm({ ...active, enabled: v })} />
            <span className="text-sm text-muted-foreground">Alerts</span>
          </div>
          <Button
            loading={saveMut.isPending}
            onClick={() => saveMut.mutate({
              admin_email: active.admin_email || null,
              cooldown_minutes: Number(active.cooldown_minutes),
              enabled: active.enabled,
            })}
          >
            <Save className="h-4 w-4" /> Save
          </Button>
        </div>

        {/* Per-service 24h uptime */}
        {history.isError ? (
          <ErrorState error={history.error} onRetry={history.refetch} />
        ) : (
          <div className="space-y-2">
            {services.map((s) => (
              <div key={s.service} className="flex flex-wrap items-center gap-3 rounded-btn border border-border px-3 py-2">
                <div className="w-24 shrink-0">
                  <div className="text-sm font-medium text-foreground">{s.service}</div>
                  <div className="text-[11px] text-muted-foreground">{s.unit}</div>
                </div>
                {s.currently_down
                  ? <Badge variant="danger">Down{s.down_since ? ` since ${formatDate(s.down_since)}` : ''}</Badge>
                  : <Badge variant="success">Up</Badge>}
                <div className="flex-1" />
                <UptimeSparkline series={s.series} />
                <div className="w-32 text-right">
                  <div className="text-sm font-semibold tabular-nums text-foreground">
                    {s.uptime_pct != null ? `${s.uptime_pct}%` : '—'}
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    {s.last_alert_sent_at ? `alerted ${formatDate(s.last_alert_sent_at)}` : 'no alerts sent'}
                  </div>
                </div>
              </div>
            ))}
            {!services.length && !history.isLoading && (
              <p className="text-sm text-muted-foreground">
                No checks recorded yet — the */5 cron (deploy/boron-monitoring.cron) hasn't run.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export default function ServerHealth() {
  const health = useQuery({ queryKey: ['health'], queryFn: () => get('/api/v1/health'), refetchInterval: 10_000 })
  const history = useQuery({ queryKey: ['health-history'], queryFn: () => get('/api/v1/health/history?hours=24') })

  const h = health.data
  const rootDisk = h?.disks?.find((d) => d.mount === '/') || h?.disks?.[0]

  const points = (history.data?.points || []).map((p) => ({
    label: p.taken_at ? new Date(p.taken_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '',
    cpu: Math.round(p.cpu_pct ?? 0),
    mem: p.mem_total_bytes ? Math.round(((p.mem_used_bytes || 0) / p.mem_total_bytes) * 100) : 0,
    net: (p.net_rx_delta || 0) + (p.net_tx_delta || 0),
  }))

  return (
    <div>
      <PageHeader title="Server Health" description="Live resource utilisation and 24-hour trends." icon={Activity} />

      {health.isError ? (
        <ErrorState error={health.error} onRetry={health.refetch} />
      ) : health.isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => <CardSkeleton key={i} />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Gauge icon={Cpu} label="CPU" pct={h.cpu_pct} detail={`${h.cpu_count} cores`} />
          <Gauge icon={MemoryStick} label="Memory" pct={h.mem_pct} detail={`${formatBytes(h.mem_used)} / ${formatBytes(h.mem_total)}`} />
          <Gauge icon={HardDrive} label="Disk (/)" pct={rootDisk?.pct} detail={rootDisk ? `${formatBytes(rootDisk.used)} / ${formatBytes(rootDisk.total)}` : ''} />
          <Card>
            <CardContent className="py-5">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <ArrowDownUp className="h-4 w-4" /> Network
              </div>
              <div className="mt-3 space-y-1 text-sm">
                <div className="flex justify-between"><span className="text-muted-foreground">RX</span><span className="font-medium tabular-nums">{formatBytes(h.net_rx_bytes)}</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">TX</span><span className="font-medium tabular-nums">{formatBytes(h.net_tx_bytes)}</span></div>
              </div>
              <div className="mt-2 flex items-center gap-1 text-xs text-muted-foreground">
                <Clock className="h-3 w-3" /> up {formatDuration(h.uptime_seconds)}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Disks table */}
      {h?.disks?.length > 1 && (
        <Card className="mt-6">
          <CardHeader><CardTitle>Filesystems</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            {h.disks.map((d) => (
              <div key={d.mount}>
                <div className="flex items-center justify-between text-sm">
                  <span className="font-medium text-foreground">{d.mount} <span className="text-muted-foreground">· {d.device} ({d.fstype})</span></span>
                  <span className="tabular-nums text-muted-foreground">{formatBytes(d.used)} / {formatBytes(d.total)}</span>
                </div>
                <ProgressBar value={d.pct} className="mt-1.5" />
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <MonitoringCard />

      <CloudflareCard />

      {/* 24h charts */}
      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
        {history.isLoading ? (
          <><CardSkeleton /><CardSkeleton /></>
        ) : history.isError ? (
          <ErrorState error={history.error} onRetry={history.refetch} className="lg:col-span-2" />
        ) : (
          <>
            <ChartCard title="CPU usage (24h)" data={points} dataKey="cpu" color="#1FBED6" formatY={(v) => `${v}%`} />
            <ChartCard title="Memory usage (24h)" data={points} dataKey="mem" color="#10B981" formatY={(v) => `${v}%`} />
            <ChartCard title="Network throughput (24h)" data={points} dataKey="net" color="#F59E0B" formatY={(v) => formatBytes(v)} />
          </>
        )}
      </div>
    </div>
  )
}
