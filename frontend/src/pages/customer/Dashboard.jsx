import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  HardDrive, Gauge, Globe, Mail, Database, ShieldCheck, Boxes, AlertTriangle, ArrowRight, Cpu,
} from 'lucide-react'
import { get } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatBytes, formatMB, percent } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { ProgressBar } from '@/components/ui/Progress'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Badge } from '@/components/ui/Badge'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { ErrorState } from '@/components/ui/States'
import { OnboardingWizard } from '@/components/onboarding/OnboardingWizard'

const QUICK_ACTIONS = [
  { label: 'Domains', to: '/domains', icon: Globe },
  { label: 'Email', to: '/email', icon: Mail },
  { label: 'Databases', to: '/databases', icon: Database },
  { label: 'SSL', to: '/ssl', icon: ShieldCheck },
  { label: 'Applications', to: '/apps', icon: Boxes },
  { label: 'Files', to: '/files', icon: HardDrive },
]

const ALERT_DESTINATIONS = {
  disk: '/disk-usage',
  disk_space: '/disk-usage',
  bandwidth: '/dashboard',
  database: '/databases',
  email: '/email',
}

function StatCard({ icon: Icon, label, value, sub }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 py-5">
        <div className="flex h-11 w-11 items-center justify-center rounded-btn bg-accent-50 text-accent-600 dark:bg-accent-950 dark:text-accent-300">
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
          <div className="mt-0.5 truncate text-lg font-semibold text-foreground">{value}</div>
          {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
        </div>
      </CardContent>
    </Card>
  )
}

export default function Dashboard() {
  const username = useAccountUsername()
  const account = useQuery({ queryKey: ['account', username], queryFn: () => get(`/api/v1/accounts/${username}`), enabled: !!username })
  const usage = useQuery({ queryKey: ['usage', username], queryFn: () => get(`/api/v1/accounts/${username}/usage`), enabled: !!username })
  const alerts = useQuery({ queryKey: ['alerts', username], queryFn: () => get(`/api/v1/accounts/${username}/alerts`), enabled: !!username })
  const domains = useQuery({ queryKey: ['domains', username], queryFn: () => get(`/api/v1/accounts/${username}/domains`), enabled: !!username })

  const acc = account.data
  const u = usage.data
  const activeAlerts = alerts.data?.active || []
  const domainCount = domains.data?.domains?.length ?? 0

  const diskUsed = u?.current?.disk_total_bytes ?? 0
  const diskQuotaMb = u?.quota_hard_mb ?? acc?.quota_hard_mb ?? 0
  const diskQuotaBytes = diskQuotaMb * 1024 * 1024
  const bwMtd = u?.bandwidth_month_to_date_bytes ?? 0

  return (
    <div>
      {/* Run A feature 4: first-login onboarding (renders nothing once completed). */}
      {username && <OnboardingWizard username={username} account={acc} />}

      <PageHeader title={`Welcome back${username ? `, ${username}` : ''}`} description="Here's an overview of your hosting account.">
        {acc && <StatusBadge status={acc.status} />}
      </PageHeader>

      {activeAlerts.length > 0 && (
        <Card className="mb-6 border-danger/40">
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-danger" />
              <div className="flex-1">
                <div className="font-medium text-foreground">Usage alerts</div>
                <ul className="mt-1 space-y-1 text-sm text-muted-foreground">
                  {activeAlerts.map((a) => (
                    <li key={a.id}>
                      <Link to={ALERT_DESTINATIONS[a.resource] || '/dashboard'} className="group flex items-center gap-2 rounded-btn py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                        <Badge variant={a.threshold_pct >= 100 ? 'danger' : 'warning'}>{a.threshold_pct}%</Badge>
                        <span className="capitalize">{a.resource}</span> reached its limit
                        <ArrowRight className="ml-auto h-4 w-4 transition-transform group-hover:translate-x-0.5" />
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {!domains.isLoading && domainCount === 0 && (
        <Card className="mb-6 border-accent/40 bg-accent-50/60 dark:bg-accent-950/20">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
            <div>
              <div className="font-medium text-foreground">Finish setting up your hosting</div>
              <p className="mt-0.5 text-sm text-muted-foreground">Add a domain first, then configure email and SSL.</p>
            </div>
            <Link to="/domains" className="inline-flex min-h-9 items-center gap-2 rounded-btn bg-accent px-3 text-sm font-medium text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              Add your first domain <ArrowRight className="h-4 w-4" />
            </Link>
          </CardContent>
        </Card>
      )}

      {account.isError ? (
        <ErrorState error={account.error} onRetry={account.refetch} />
      ) : account.isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => <CardSkeleton key={i} />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard icon={Globe} label="Domains" value={domainCount} sub={acc?.primary_domain || 'No primary domain'} />
          <StatCard icon={Cpu} label="PHP Version" value={acc?.php_version ? `PHP ${acc.php_version}` : '—'} />
          <StatCard icon={HardDrive} label="Disk Used" value={formatBytes(diskUsed)} sub={diskQuotaMb ? `of ${formatMB(diskQuotaMb)}` : 'unlimited'} />
          <StatCard icon={Gauge} label="Bandwidth (MTD)" value={formatBytes(bwMtd)} />
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Resource usage</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            {usage.isLoading ? (
              <CardSkeleton className="border-0 shadow-none p-0" />
            ) : usage.isError ? (
              <ErrorState error={usage.error} onRetry={usage.refetch} />
            ) : (
              <>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Disk space</span>
                    <span className="font-medium tabular-nums">
                      {formatBytes(diskUsed)} {diskQuotaBytes ? <span className="text-muted-foreground">/ {formatMB(diskQuotaMb)}</span> : null}
                    </span>
                  </div>
                  <ProgressBar value={diskQuotaBytes ? percent(diskUsed, diskQuotaBytes) : 0} />
                </div>
                <div className="grid grid-cols-3 gap-4 pt-2">
                  <div>
                    <div className="text-xs text-muted-foreground">Home</div>
                    <div className="text-sm font-medium">{formatBytes(u?.current?.disk_home_bytes)}</div>
                  </div>
                  <div>
                    <div className="text-xs text-muted-foreground">Databases</div>
                    <div className="text-sm font-medium">{formatBytes(u?.current?.disk_db_bytes)}</div>
                  </div>
                  <div>
                    <div className="text-xs text-muted-foreground">Mail</div>
                    <div className="text-sm font-medium">{formatBytes(u?.current?.disk_mail_bytes)}</div>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Quick actions</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-2">
              {QUICK_ACTIONS.map((a) => (
                <Link
                  key={a.to}
                  to={a.to}
                  className="group flex flex-col gap-2 rounded-btn border border-border p-3 transition-colors hover:border-accent hover:bg-accent-50 dark:hover:bg-accent-950/40"
                >
                  <a.icon className="h-5 w-5 text-accent-600" />
                  <span className="flex items-center justify-between text-sm font-medium text-foreground">
                    {a.label}
                    <ArrowRight className="h-3.5 w-3.5 opacity-0 transition-opacity group-hover:opacity-100" />
                  </span>
                </Link>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
