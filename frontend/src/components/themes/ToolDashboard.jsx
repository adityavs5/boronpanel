import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ArrowUpRight, ShieldCheck, AlertTriangle, RefreshCw, ExternalLink, UserRound, Gauge, Compass } from 'lucide-react'
import { get } from '@/lib/api'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { getToolGroups } from '@/config/toolGroups'
import { toolIcon, toolGroupMeta } from '@/config/toolIcons'
import { useVersion } from '@/hooks/useVersion'
import { useBranding } from '@/hooks/useBranding'
import { formatBytes, formatMB } from '@/lib/utils'
import { ThemeSelector } from './ThemeSelector'
import { OnboardingWizard } from '@/components/onboarding/OnboardingWizard'
import { DashboardSearch } from './DashboardSearch'
import { getProductBrandIcon } from '@/components/icons/BrandIcons'

const ACRONYMS = new Set(['DNS', 'PHP', 'IP', 'FTP', 'SSL', 'SSH', 'API', 'WAF', 'IMAP', 'DB'])
// Group titles are shown in sentence case (DESIGN.md), keeping acronyms intact.
export function sentenceCase(title) {
  return title.split(' ').map((word, index) => index === 0 || ACRONYMS.has(word) ? word : word.toLowerCase()).join(' ')
}

// Category chip: a lucide stroke icon on a soft hue tint. Product marks sit in a neutral chip.
export function ToolIcon({ icon, to }) {
  const Brand = getProductBrandIcon(to)
  if (Brand) return <span className="tool-icon tool-icon-brand" aria-hidden="true"><Brand /></span>
  const Icon = toolIcon(to, icon)
  return <span className="tool-icon" aria-hidden="true">{Icon && <Icon strokeWidth={1.75} />}</span>
}

function ToolGroup({ group, index, role, skin, stackUpdateCount = 0 }) {
  const key = `${skin}:${role}:${group.title}`
  const closed = useUI((s) => !!s.closedToolGroups[key])
  const toggle = useUI((s) => s.toggleToolGroup)
  const { icon: GroupIcon, hue } = toolGroupMeta(group.title, role, index)
  const expanded = !closed
  const title = sentenceCase(group.title)
  const id = `tools-${group.title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`
  return <section className={`tool-group hue-${hue}`} aria-label={title}>
    <h2 className="tool-group-title"><button type="button" className="tool-group-heading" aria-expanded={expanded} aria-controls={id} onClick={() => toggle(key)}>
      <span className="group-chip" aria-hidden="true"><GroupIcon strokeWidth={1.75} /></span>
      <span className="group-title">{title}</span>
      <span className="group-count">{group.items.length} {group.items.length === 1 ? 'tool' : 'tools'}</span>
      <ChevronDown className="group-chevron" aria-hidden="true" />
    </button></h2>
    <div id={id} className="tool-grid" hidden={!expanded}>
      {group.items.map((item) => {
        const count = item.to === '/stack-manager' ? stackUpdateCount : 0
        const content = <><ToolIcon icon={item.icon} to={item.to} />{count > 0 && <span className="tool-update-count" aria-label={`${count} stack updates available`}>{count}</span>}<span className="tool-label">{item.label}</span>{item.external && <ExternalLink className="tool-external" size={12} aria-label="Opens in a new tab" />}</>
        return item.external
          ? <a key={item.to} href={item.to} target="_blank" rel="noopener noreferrer" className="tool-link">{content}</a>
          : <Link key={item.to} to={item.to} className="tool-link">{content}</Link>
      })}
    </div>
  </section>
}

function InfoRow({ label, children }) {
  return <div className="info-row"><dt>{label}</dt><dd>{children ?? '—'}</dd></div>
}
function UsageRow({ label, value, pct, detail }) {
  const validPct = Number.isFinite(pct)
  const bounded = validPct ? Math.min(100, Math.max(0, pct)) : 0
  // Meter colour by threshold: accent below 70%, warning from 70%, danger from 90%.
  const level = !validPct ? undefined : pct >= 90 ? 'danger' : pct >= 70 ? 'warning' : 'ok'
  return <div className="usage-row" data-level={level}>
    <div className="usage-line"><span>{label}</span><strong>{value ?? '—'}</strong></div>
    {validPct && <div className="usage-track" role="progressbar" aria-label={label} aria-valuenow={Math.round(bounded)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${bounded}%` }} /></div>}
    {detail && <small>{detail}</small>}
  </div>
}
function StatsPanel({ title, icon: Icon, children, link, kind = 'info' }) {
  return <section className={`stats-panel stats-${kind}`} aria-label={title}>
    <h2 className="stats-heading">{Icon && <span className="stats-chip" aria-hidden="true"><Icon strokeWidth={1.75} /></span>}{title}</h2>
    <div className="stats-body">{children}</div>
    {link && <Link className="stats-link" to={link.to}>{link.label}<ArrowUpRight size={14} aria-hidden="true" /></Link>}
  </section>
}
function QueryNotice({ query, label }) {
  if (query.isError) return <div className="query-notice" role="status"><span>{label} unavailable.</span><button type="button" onClick={() => query.refetch()} aria-label={`Retry ${label.toLowerCase()}`}><RefreshCw size={13} aria-hidden="true" /> Retry</button></div>
  if (query.isPending) return <p className="query-notice" role="status">Loading {label.toLowerCase()}…</p>
  return null
}

export default function ToolDashboard() {
  const { role, username } = useAuth()
  const skin = useUI((s) => s.skin)
  const isAdmin = role === 'admin'
  const { panelName, supportUrl, supportEmail } = useBranding()
  const version = useVersion()
  const capabilities = useAuth((state) => state.capabilities)
  const allGroups = useMemo(() => getToolGroups(role, skin, capabilities), [role, skin, capabilities])
  const options = { retry: false, staleTime: 30_000 }
  const health = useQuery({ queryKey: ['health'], queryFn: () => get('/api/v1/health'), enabled: isAdmin, refetchInterval: 30_000, ...options })
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => get('/api/v1/accounts'), enabled: isAdmin, ...options })
  const stack = useQuery({ queryKey: ['stack-manager'], queryFn: () => get('/api/v1/admin/stack'), enabled: isAdmin, staleTime: 5 * 60_000, retry: false })
  const account = useQuery({ queryKey: ['account', username], queryFn: () => get(`/api/v1/accounts/${username}`), enabled: !isAdmin && !!username, ...options })
  const usage = useQuery({ queryKey: ['usage', username], queryFn: () => get(`/api/v1/accounts/${username}/usage`), enabled: !isAdmin && !!username, refetchInterval: 5000, ...options })
  const alerts = useQuery({ queryKey: ['alerts', username], queryFn: () => get(`/api/v1/accounts/${username}/alerts`), enabled: !isAdmin && !!username, ...options })
  const disk = health.data?.disks?.find((d) => d.mount === '/') || health.data?.disks?.[0]
  const acc = account.data
  const u = usage.data
  const counters = u?.resources
  const previousCounters = useRef(null)
  const [rates, setRates] = useState({})
  useEffect(() => {
    if (!counters?.sampled_at) return
    const previous = previousCounters.current
    if (previous) {
      const seconds = (Date.parse(counters.sampled_at) - Date.parse(previous.sampled_at)) / 1000
      if (seconds > 0) setRates({
        cpuCores: counters.cpu_usage_usec != null && previous.cpu_usage_usec != null ? Math.max(0, counters.cpu_usage_usec - previous.cpu_usage_usec) / (seconds * 1_000_000) : null,
        ioBytes: [counters.read_bytes, counters.write_bytes, previous.read_bytes, previous.write_bytes].every(value => value != null) ? Math.max(0, counters.read_bytes + counters.write_bytes - previous.read_bytes - previous.write_bytes) / seconds : null,
        iops: [counters.read_ops, counters.write_ops, previous.read_ops, previous.write_ops].every(value => value != null) ? Math.max(0, counters.read_ops + counters.write_ops - previous.read_ops - previous.write_ops) / seconds : null,
      })
    }
    previousCounters.current = counters
  }, [counters?.sampled_at, counters?.cpu_usage_usec, counters?.read_bytes, counters?.write_bytes, counters?.read_ops, counters?.write_ops])
  const diskUsed = u?.current?.disk_total_bytes
  const diskQuota = u?.quota_hard_mb ?? acc?.quota_hard_mb
  return <div className={`tools-dashboard dashboard-${isAdmin ? 'admin' : 'customer'}`}>
    {!isAdmin && username && <OnboardingWizard username={username} account={acc} />}
    <div className="dashboard-heading"><div><h1>{isAdmin ? 'Admin dashboard' : 'Hosting dashboard'}</h1><p>{isAdmin ? 'Manage your server, accounts and hosting services.' : `Welcome${username ? `, ${username}` : ''}. Everything you need to manage your hosting.`}</p></div><span className="dashboard-role">{isAdmin ? <ShieldCheck size={14} aria-hidden="true" /> : <UserRound size={14} aria-hidden="true" />} {isAdmin ? 'Administrator' : 'User account'}</span></div>
    {!isAdmin && alerts.data?.active?.length > 0 && <div className="dashboard-alert" role="status"><AlertTriangle size={18} aria-hidden="true" /><div><strong>Resource usage needs attention</strong>{alerts.data.active.map((alert, index) => <p key={alert.id ?? index}>{alert.resource}: {alert.threshold_pct}% threshold reached.</p>)}<Link to="/disk-usage">Review resource usage</Link></div></div>}
    <DashboardSearch />
    <div className="dashboard-columns">
      <div className="tools-column">
        {allGroups.map((group, index) => <ToolGroup key={group.title} group={group} index={index} role={role} skin={skin} stackUpdateCount={stack.data?.pending_update_count || 0} />)}
      </div>
      <aside className="dashboard-stats" aria-label="Account and resource overview">
        <StatsPanel title="Your account" icon={UserRound} link={{ to: isAdmin ? '/health' : '/domains', label: isAdmin ? 'Full server information' : 'Manage domains' }}>
          <dl><InfoRow label="Current user"><span className="current-user">{username || '—'}</span></InfoRow>
            <InfoRow label="Access level">{isAdmin ? 'Administrator' : 'User'}</InfoRow>
            {!isAdmin && <><InfoRow label="Primary domain">{acc?.primary_domain || (account.isPending ? 'Loading…' : 'Not configured')}</InfoRow><InfoRow label="Home directory">{account.isSuccess && username ? `/home/${username}` : '—'}</InfoRow><InfoRow label="PHP version">{acc?.php_version ? `PHP ${acc.php_version}` : '—'}</InfoRow><QueryNotice query={account} label="Account information" /></>}
            <InfoRow label="Theme"><ThemeSelector /></InfoRow>
          </dl>
        </StatsPanel>
        <StatsPanel kind="resources" title="Resource usage" icon={Gauge}>
          {isAdmin ? <><QueryNotice query={health} label="Server statistics" />
            {health.isSuccess && <><UsageRow label="CPU usage" value={health.data.cpu_pct != null ? `${Math.round(health.data.cpu_pct)}%` : '—'} pct={health.data.cpu_pct} /><UsageRow label="Memory usage" value={health.data.mem_pct != null ? `${Math.round(health.data.mem_pct)}%` : '—'} pct={health.data.mem_pct} /><UsageRow label="Disk space" value={disk?.pct != null ? `${Math.round(disk.pct)}%` : '—'} pct={disk?.pct} /></>}
            <QueryNotice query={accounts} label="Accounts" />
            {accounts.isSuccess && <><UsageRow label="Hosting accounts" value={Array.isArray(accounts.data) ? accounts.data.length : '—'} /><UsageRow label="Active accounts" value={Array.isArray(accounts.data) ? accounts.data.filter((a) => a.status === 'active').length : '—'} /></>}
          </> : <><QueryNotice query={usage} label="Usage statistics" /><>
            <UsageRow label="CPU" value={rates.cpuCores != null ? `${rates.cpuCores.toFixed(2)} cores` : 'Collecting…'} pct={rates.cpuCores != null && counters?.cpu_limit_cores ? rates.cpuCores / counters.cpu_limit_cores * 100 : undefined} detail={counters?.cpu_limit_cores ? `${counters.cpu_limit_cores} cores allocated` : undefined} />
            <UsageRow label="Memory" value={counters?.memory_current_bytes != null ? formatBytes(counters.memory_current_bytes) : '—'} pct={counters?.memory_current_bytes != null && counters?.memory_limit_bytes ? counters.memory_current_bytes / counters.memory_limit_bytes * 100 : undefined} detail={counters?.memory_limit_bytes ? `of ${formatBytes(counters.memory_limit_bytes)}` : undefined} />
            <UsageRow label="Disk I/O" value={rates.ioBytes != null ? `${formatBytes(rates.ioBytes)}/s` : 'Collecting…'} detail={counters?.io_limit_bytes_per_second ? `${formatBytes(counters.io_limit_bytes_per_second)}/s limit` : undefined} />
            <UsageRow label="IOPS" value={rates.iops != null ? `${Math.round(rates.iops)} ops/s` : 'Collecting…'} />
            <UsageRow label="Disk space" value={diskUsed != null ? formatBytes(diskUsed) : '—'} pct={diskQuota > 0 && diskUsed != null ? diskUsed / (diskQuota * 1024 * 1024) * 100 : undefined} detail={diskQuota > 0 ? `of ${formatMB(diskQuota)}` : diskQuota === 0 ? 'No disk limit' : undefined} />
            <UsageRow label="Bandwidth" value={u?.bandwidth_month_to_date_bytes != null ? formatBytes(u.bandwidth_month_to_date_bytes) : '—'} pct={counters?.bandwidth_limit_bytes ? u.bandwidth_month_to_date_bytes / counters.bandwidth_limit_bytes * 100 : undefined} detail={counters?.bandwidth_limit_bytes ? `of ${formatBytes(counters.bandwidth_limit_bytes)} this month` : 'This month · unlimited'} />
            <UsageRow label="Inodes" value={u?.current?.inode_count?.toLocaleString?.() ?? '—'} />
            <UsageRow label="Database disk usage" value={u?.current?.disk_db_bytes != null ? formatBytes(u.current.disk_db_bytes) : '—'} />
            <UsageRow label="Mail disk usage" value={u?.current?.disk_mail_bytes != null ? formatBytes(u.current.disk_mail_bytes) : '—'} />
            <UsageRow label="Domains" value={counters?.domain_count ?? '—'} />
            <UsageRow label="Subdomains" value={counters?.subdomain_count != null ? `${counters.subdomain_count} / ${counters.subdomain_limit ?? '∞'}` : '—'} pct={counters?.subdomain_limit ? counters.subdomain_count / counters.subdomain_limit * 100 : undefined} />
            <UsageRow label="Email accounts" value={counters?.email_account_count != null ? `${counters.email_account_count} / ${counters.email_account_limit ?? '∞'}` : '—'} pct={counters?.email_account_limit ? counters.email_account_count / counters.email_account_limit * 100 : undefined} />
            <UsageRow label="Databases" value={counters?.database_count != null ? `${counters.database_count} / ${counters.database_limit ?? '∞'}` : '—'} pct={counters?.database_limit ? counters.database_count / counters.database_limit * 100 : undefined} />
            <UsageRow label="FTP accounts" value={counters?.ftp_account_count != null ? `${counters.ftp_account_count} / ${counters.ftp_account_limit ?? '∞'}` : '—'} pct={counters?.ftp_account_limit ? counters.ftp_account_count / counters.ftp_account_limit * 100 : undefined} />
          </></>}
        </StatsPanel>
        <StatsPanel kind="links" title="Quick links" icon={Compass}>
          <div className="stats-quick-links"><Link to={isAdmin ? '/accounts' : '/files'}>{isAdmin ? 'Manage accounts' : 'Open file manager'}<ArrowUpRight size={14} /></Link><Link to="/security">Secure your account<ArrowUpRight size={14} /></Link><Link to="/appearance">Customize your workspace<ArrowUpRight size={14} /></Link>{supportUrl && <a href={supportUrl} target="_blank" rel="noopener noreferrer">Contact support<ExternalLink size={14} /></a>}{!supportUrl && supportEmail && <a href={`mailto:${supportEmail}`}>Contact support<ExternalLink size={14} /></a>}</div>
        </StatsPanel>
      </aside>
    </div>
    <footer className="dashboard-footer"><span>{panelName} web control panel</span><span>Version {version}</span></footer>
  </div>
}
