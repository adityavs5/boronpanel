import { useQuery } from '@tanstack/react-query'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip as RTooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { Cpu, MemoryStick, HardDrive, Activity, ArrowDownUp, Clock } from 'lucide-react'
import { get } from '@/lib/api'
import { formatBytes, formatDuration, formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { ProgressBar } from '@/components/ui/Progress'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { ErrorState } from '@/components/ui/States'

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
