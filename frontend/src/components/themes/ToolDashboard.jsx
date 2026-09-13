import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Search, X, ChevronDown, Plus, Minus, ArrowUpRight, Server, AlertTriangle, RefreshCw, ExternalLink } from 'lucide-react'
import { get } from '@/lib/api'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { searchScore } from '@/config/search'
import { getToolGroups } from '@/config/toolGroups'
import { useVersion } from '@/hooks/useVersion'
import { useBranding } from '@/hooks/useBranding'
import { formatBytes, formatMB } from '@/lib/utils'
import { ThemeSelector } from './ThemeSelector'
import { OnboardingWizard } from '@/components/onboarding/OnboardingWizard'

export function ToolIcon({ icon: Icon, tone = 'sky' }) {
  return <span className={`tool-icon tone-${tone}`} aria-hidden="true"><Icon strokeWidth={1.7} /><span className="icon-detail" /></span>
}

function ToolGroup({ group, searching, role, skin }) {
  const key = `${skin}:${role}:${group.title}`
  const closed = useUI((s) => !!s.closedToolGroups[key])
  const toggle = useUI((s) => s.toggleToolGroup)
  const expanded = searching || !closed
  const id = `tools-${group.title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`
  return <section className="tool-group" aria-label={group.title}>
    <h2><button type="button" className="tool-group-heading" aria-expanded={expanded} aria-controls={id} onClick={() => toggle(key)} disabled={searching}>
      <span>{group.title}</span>
      <span className="group-count">{group.items.length} tools</span>
      {skin === 'paper-lantern' ? (expanded ? <Minus size={16} /> : <Plus size={16} />) : <ChevronDown size={16} className={expanded ? '' : '-rotate-90'} />}
    </button></h2>
    <div id={id} className="tool-grid" hidden={!expanded}>
      {group.items.map((item) => {
        const content = <><ToolIcon icon={item.icon} tone={item.tone} /><span className="tool-label">{item.label}</span>{item.external && <ExternalLink className="tool-external" size={11} aria-label="Opens in a new tab" />}</>
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
  return <div className="usage-row">
    <div><span>{label}</span><strong>{value ?? '—'}</strong></div>
    {validPct && <div className="usage-track" role="progressbar" aria-label={label} aria-valuenow={Math.round(Math.min(100, Math.max(0, pct)))} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} className={pct >= 90 ? 'usage-high' : ''} /></div>}
    {detail && <small>{detail}</small>}
  </div>
}
function StatsPanel({ title, children, link }) {
  return <section className="stats-panel" aria-label={title}><h2 className="stats-heading">{title}</h2><div className="stats-body">{children}</div>{link && <Link className="stats-link" to={link.to}>{link.label}<ArrowUpRight size={14} /></Link>}</section>
}
function QueryNotice({ query, label }) {
  if (query.isError) return <div className="query-notice" role="status"><span>{label} unavailable.</span><button type="button" onClick={() => query.refetch()} aria-label={`Retry ${label.toLowerCase()}`}><RefreshCw size={13} /> Retry</button></div>
  if (query.isPending) return <p className="query-notice" role="status">Loading {label.toLowerCase()}…</p>
  return null
}

export default function ToolDashboard() {
  const { role, username } = useAuth()
  const skin = useUI((s) => s.skin)
  const [search, setSearch] = useState('')
  const isAdmin = role === 'admin'
  const { panelName, supportUrl, supportEmail } = useBranding()
  const version = useVersion()
  const query = search.trim().toLowerCase()
  const allGroups = useMemo(() => getToolGroups(role, skin), [role, skin])
  const groups = useMemo(() => allGroups.map((group) => ({ ...group, items: group.items.filter((item) => searchScore({ ...item, group: group.title }, query) > 0) })).filter((group) => group.items.length), [allGroups, query])
  const options = { retry: false, staleTime: 30_000 }
  const health = useQuery({ queryKey: ['health'], queryFn: () => get('/api/v1/health'), enabled: isAdmin, refetchInterval: 30_000, ...options })
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => get('/api/v1/accounts'), enabled: isAdmin, ...options })
  const account = useQuery({ queryKey: ['account', username], queryFn: () => get(`/api/v1/accounts/${username}`), enabled: !isAdmin && !!username, ...options })
  const usage = useQuery({ queryKey: ['usage', username], queryFn: () => get(`/api/v1/accounts/${username}/usage`), enabled: !isAdmin && !!username, ...options })
  const domains = useQuery({ queryKey: ['domains', username], queryFn: () => get(`/api/v1/accounts/${username}/domains`), enabled: !isAdmin && !!username, ...options })
  const alerts = useQuery({ queryKey: ['alerts', username], queryFn: () => get(`/api/v1/accounts/${username}/alerts`), enabled: !isAdmin && !!username, ...options })
  const disk = health.data?.disks?.find((d) => d.mount === '/') || health.data?.disks?.[0]
  const acc = account.data
  const u = usage.data
  const diskUsed = u?.current?.disk_total_bytes
  const diskQuota = u?.quota_hard_mb ?? acc?.quota_hard_mb
  const count = groups.reduce((n, group) => n + group.items.length, 0)
  return <div className="tools-dashboard">
    {!isAdmin && username && <OnboardingWizard username={username} account={acc} />}
    <div className="dashboard-heading"><div><h1>{isAdmin ? 'Admin Dashboard' : 'Hosting Dashboard'}</h1><p>{isAdmin ? 'Manage your server, accounts, and hosting services.' : `Welcome${username ? `, ${username}` : ''}. Everything you need to manage your hosting.`}</p></div><span className="dashboard-role"><Server size={14} /> {isAdmin ? 'Administrator' : 'User account'}</span></div>
    {!isAdmin && alerts.data?.active?.length > 0 && <div className="dashboard-alert" role="status"><AlertTriangle size={18} /><div><strong>Resource usage needs attention</strong>{alerts.data.active.map((alert, index) => <p key={alert.id ?? index}>{alert.resource}: {alert.threshold_pct}% threshold reached.</p>)}<Link to="/disk-usage">Review resource usage</Link></div></div>}
    <div className="dashboard-columns">
      <div className="tools-column">
        <div className="tool-filter"><Search size={19} aria-hidden="true" /><input aria-label="Filter tools" placeholder="What would you like to do? Try WordPress, DNS zone editor, or email…" value={search} onChange={(e) => setSearch(e.target.value)} />{search && <button type="button" aria-label="Clear tool filter" onClick={() => setSearch('')}><X size={17} /></button>}<span className="filter-shortcut">Search tools</span></div>
        {query && <p className="filter-results" role="status">{count} {count === 1 ? 'tool' : 'tools'} found</p>}
        {groups.map((group) => <ToolGroup key={group.title} group={group} searching={!!query} role={role} skin={skin} />)}
        {groups.length === 0 && <div className="tools-empty"><Search size={32} /><h2>No tools found</h2><p>Try a different name, such as “{isAdmin ? 'accounts' : 'files'}”.</p><button type="button" onClick={() => setSearch('')}>Show all tools</button></div>}
      </div>
      <aside className="dashboard-stats" aria-label="Account and resource overview">
        <StatsPanel title={skin === 'evolution' ? (isAdmin ? 'Admin Stats' : 'Your Account') : 'General Information'} link={{ to: isAdmin ? '/health' : '/domains', label: isAdmin ? 'Full server information' : 'Manage domains' }}>
          <dl><InfoRow label="Current User"><span className="current-user">{username || '—'}</span></InfoRow>
            <InfoRow label="Access Level">{isAdmin ? 'Administrator' : 'User'}</InfoRow>
            {!isAdmin && <><InfoRow label="Primary Domain">{acc?.primary_domain || (account.isPending ? 'Loading…' : 'Not configured')}</InfoRow><InfoRow label="Home Directory">{account.isSuccess && username ? `/home/${username}` : '—'}</InfoRow><InfoRow label="PHP Version">{acc?.php_version ? `PHP ${acc.php_version}` : '—'}</InfoRow><QueryNotice query={account} label="Account information" /></>}
            <InfoRow label="Theme"><ThemeSelector /></InfoRow>
          </dl>
        </StatsPanel>
        <StatsPanel title={skin === 'evolution' ? 'Resource Usage' : 'Statistics'}>
          {isAdmin ? <><QueryNotice query={health} label="Server statistics" />
            {health.isSuccess && <><UsageRow label="CPU Usage" value={health.data.cpu_pct != null ? `${Math.round(health.data.cpu_pct)}%` : '—'} pct={health.data.cpu_pct} /><UsageRow label="Memory Usage" value={health.data.mem_pct != null ? `${Math.round(health.data.mem_pct)}%` : '—'} pct={health.data.mem_pct} /><UsageRow label="Disk Space" value={disk?.pct != null ? `${Math.round(disk.pct)}%` : '—'} pct={disk?.pct} /></>}
            <QueryNotice query={accounts} label="Accounts" />
            {accounts.isSuccess && <><UsageRow label="Hosting Accounts" value={Array.isArray(accounts.data) ? accounts.data.length : '—'} /><UsageRow label="Active Accounts" value={Array.isArray(accounts.data) ? accounts.data.filter((a) => a.status === 'active').length : '—'} /></>}
          </> : <><QueryNotice query={usage} label="Usage statistics" />{usage.isSuccess && <>
            <UsageRow label="Disk Space" value={diskUsed != null ? formatBytes(diskUsed) : '—'} pct={diskQuota > 0 && diskUsed != null ? diskUsed / (diskQuota * 1024 * 1024) * 100 : undefined} detail={diskQuota > 0 ? `of ${formatMB(diskQuota)}` : diskQuota === 0 ? 'No disk limit' : undefined} />
            <UsageRow label="Bandwidth" value={u?.bandwidth_month_to_date_bytes != null ? formatBytes(u.bandwidth_month_to_date_bytes) : '—'} detail="This month" />
            <UsageRow label="Database Disk Usage" value={u?.current?.disk_db_bytes != null ? formatBytes(u.current.disk_db_bytes) : '—'} />
            <UsageRow label="Mail Disk Usage" value={u?.current?.disk_mail_bytes != null ? formatBytes(u.current.disk_mail_bytes) : '—'} />
          </>}<QueryNotice query={domains} label="Domains" />{domains.isSuccess && <UsageRow label="Domains" value={domains.data?.domains?.length ?? '—'} />}</>}
        </StatsPanel>
        <StatsPanel title="Quick Links">
          <div className="stats-quick-links"><Link to={isAdmin ? '/accounts' : '/files'}>{isAdmin ? 'Manage accounts' : 'Open file manager'}<ArrowUpRight size={14} /></Link><Link to="/security">Secure your account<ArrowUpRight size={14} /></Link><Link to="/appearance">Customize your workspace<ArrowUpRight size={14} /></Link>{supportUrl && <a href={supportUrl} target="_blank" rel="noopener noreferrer">Contact support<ExternalLink size={14} /></a>}{!supportUrl && supportEmail && <a href={`mailto:${supportEmail}`}>Contact support<ExternalLink size={14} /></a>}</div>
        </StatsPanel>
      </aside>
    </div>
    <footer className="dashboard-footer"><span>{panelName} web control panel</span><span>Version {version}</span></footer>
  </div>
}
