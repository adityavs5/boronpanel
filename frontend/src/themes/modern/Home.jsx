import { Link } from 'react-router-dom'
import { ArrowUpRight } from 'lucide-react'
import { OnboardingWizard } from '@/components/onboarding/OnboardingWizard'
import { DashboardSearch } from '@/components/themes/DashboardSearch'
import { formatBytes } from '@/lib/utils'
import { AccountPanel, DashboardAlert, DashboardFooter, QuickLinksPanel, ResourcePanel, ToolGroup, ToolIcon, useDashboardData } from '../shared/ToolsHome'

function greeting() {
  const hour = new Date().getHours()
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
}

const adminShortcuts = ['/accounts/new', '/accounts', '/services', '/updates', '/backup-jobs', '/firewall']
const customerShortcuts = ['/files', '/domains', '/email', '/databases', '/wordpress', '/backups']

function Glance({ label, value, detail, to }) {
  return <Link to={to} className="glance-card">
    <span>{label}</span>
    <strong>{value ?? '—'}</strong>
    {detail && <small>{detail}</small>}
  </Link>
}

function glances({ isAdmin, health, accounts, disk, counters, diskUsed, u }) {
  if (isAdmin) {
    const list = Array.isArray(accounts.data) ? accounts.data : null
    const pct = (value) => value != null ? `${Math.round(value)}%` : null
    return [
      { label: 'Accounts', value: list?.length, detail: list ? `${list.filter((a) => a.status === 'active').length} active` : null, to: '/accounts' },
      { label: 'CPU', value: pct(health.data?.cpu_pct), detail: 'Server load', to: '/health' },
      { label: 'Memory', value: pct(health.data?.mem_pct), detail: 'In use', to: '/health' },
      { label: 'Disk', value: pct(disk?.pct), detail: disk?.mount ? `On ${disk.mount}` : null, to: '/health' },
    ]
  }
  return [
    { label: 'Disk space', value: diskUsed != null ? formatBytes(diskUsed) : null, detail: 'Used', to: '/disk-usage' },
    { label: 'Bandwidth', value: u?.bandwidth_month_to_date_bytes != null ? formatBytes(u.bandwidth_month_to_date_bytes) : null, detail: 'This month', to: '/website-statistics' },
    { label: 'Email accounts', value: counters?.email_account_count, detail: counters?.email_account_limit ? `of ${counters.email_account_limit}` : null, to: '/email' },
    { label: 'Databases', value: counters?.database_count, detail: counters?.database_limit ? `of ${counters.database_limit}` : null, to: '/databases' },
  ]
}

// Search first, then the numbers people check most, the tools they open most,
// and every tool below, so nothing needs hunting for.
export function Home() {
  const data = useDashboardData()
  const { role, username, isAdmin, acc, allGroups, stack } = data
  const items = allGroups.flatMap((group) => group.items)
  const shortcuts = (isAdmin ? adminShortcuts : customerShortcuts).map((to) => items.find((item) => item.to === to)).filter(Boolean)
  return <div className={`tools-dashboard dashboard-${isAdmin ? 'admin' : 'customer'}`}>
    {!isAdmin && username && <OnboardingWizard username={username} account={acc} />}
    <div className="dashboard-heading"><div><h1>{greeting()}{username ? `, ${username}` : ''}</h1><p>{isAdmin ? 'Here is how your server is doing.' : 'Here is your hosting at a glance.'}</p></div></div>
    <DashboardAlert data={data} />
    <DashboardSearch />
    <div className="glance-grid">{glances(data).map((item) => <Glance key={item.label} {...item} />)}</div>
    {shortcuts.length > 0 && <section className="shortcut-section" aria-label="Common tools">
      <h2>Common tools</h2>
      <div className="shortcut-grid">{shortcuts.map((item) => <Link key={item.to} to={item.to} className="shortcut-card"><ToolIcon icon={item.icon} to={item.to} /><span>{item.label}</span><ArrowUpRight aria-hidden="true" /></Link>)}</div>
    </section>}
    <div className="dashboard-columns">
      <div className="tools-column">
        <h2 className="all-tools-title">All tools</h2>
        {allGroups.map((group, index) => <ToolGroup key={group.title} group={group} index={index} role={role} stackUpdateCount={stack.data?.pending_update_count || 0} />)}
      </div>
      <aside className="dashboard-stats" aria-label="Account and resource overview">
        <AccountPanel data={data} />
        <ResourcePanel data={data} />
        <QuickLinksPanel data={data} />
      </aside>
    </div>
    <DashboardFooter data={data} />
  </div>
}
