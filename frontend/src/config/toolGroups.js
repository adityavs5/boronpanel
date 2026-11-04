import { adminNav, customerNav } from './nav'
import { pathIsAvailable } from './capabilities'
import { Archive, CalendarClock, Cloud, DatabaseBackup, Download, FileArchive, HardDriveDownload, ListChecks, Mail, RotateCcw, BellRing } from 'lucide-react'

// One taxonomy for every theme. The home screen, navigation and search all use
// these titles.
const customerGroups = [
  ['Domains', ['/domains', '/subdomains', '/ftp', '/ssl', '/databases', '/dns', '/php']],
  ['Email', ['/email', '/email?webmail=1', '/email/settings', '/email/dns', '/email/spam', '/email/migration']],
  ['Software', ['/wordpress', '/node-apps', '/python-apps', '/redis', '/git', '/cron']],
  ['Site tools', ['/files', '/files?tool=permissions', '/redirects', '/forwarding', '/cache', '/website-maintenance', '/error-pages', '/backups']],
  ['Advanced tools', ['/ssh', '/terminal', '/logs', '/devtools', '/appearance']],
  ['Usage & security', ['/disk-usage', '/website-statistics', '/processes', '/malware', '/website-security', '/security', '/site-health']],
]
const adminGroups = [
  ['Accounts', ['/accounts/new', '/accounts', '/resellers', '/administrators', '/plans', '/import/accounts']],
  ['Server & databases', ['/health', '/diagnostics', '/services', '/openlitespeed', '/resource-manager', '/filesystem-isolation', '/security-center', '/stack-manager', '/db-monitor', '/slow-queries', '/backup-jobs', '/updates']],
  ['Domains & network', ['/dns-setup', '/cloudflare', '/dns-cluster', '/ip-management']],
  ['Email', ['/mail-queue', '/mail-tracking', '/imap-migrations', '/notifications']],
  ['Software & websites', ['/wordpress', '/software/node-apps', '/software/python-apps', '/templates', '/maintenance-mode']],
  ['Security', ['/ssl', '/malware', '/firewall', '/fail2ban', '/waf', '/ip-bans', '/ip-whitelist', '/tokens']],
  ['Logs & integrations', ['/bandwidth', '/site-stats', '/audit-log', '/account-log', '/error-log', '/retention', '/webhooks']],
  ['Preferences', ['/server-setup', '/panel-settings', '/panel-theme', '/branding', '/appearance', '/security', '/license', '/api/docs']],
]
export const TASKS_GROUP = { admin: 'Logs & integrations', customer: 'Advanced tools' }
// Home and sidebar labels: sentence case, matching each page's own title.
const labels = {
  '/git': 'Git version control', '/apps': 'Applications',
  '/accounts': 'Manage accounts', '/resellers': 'Reseller management',
  '/accounts/new': 'Create account',
}
const tones = ['sky', 'green', 'amber', 'violet', 'rose', 'teal']

// Look up links in the canonical role-aware navigation rather than creating
// synthetic tools that lead nowhere. New navigation entries get a fallback group.
export function getToolGroups(role, capabilities = []) {
  const nav = (role === 'admin' ? adminNav : customerNav).filter(item => item.section || pathIsAvailable(item.to, capabilities))
  const lookup = new Map(nav.filter((item) => item.to).map((item) => [item.to, item]))
  const definitions = role === 'admin' ? adminGroups : customerGroups
  // Change password stays available from the persistent account menu and the
  // command palette. Omitting its duplicate tile keeps every customer group at
  // six tools without hiding Appearance from dashboard search/navigation.
  const used = new Set(['/overview', '/dashboard', '/tasks', ...(role === 'customer' ? ['/change-password'] : [])])
  const groups = definitions.map(([title, paths], index) => ({
    title,
    items: paths.filter((path) => lookup.has(path)).map((path, i) => {
      used.add(path)
      return { ...lookup.get(path), label: labels[path] || lookup.get(path).label, tone: tones[(index + i) % tones.length] }
    }),
  })).filter((group) => group.items.length)
  const remaining = [...lookup.values()].filter((item) => !used.has(item.to))
  if (remaining.length) groups.push({ title: 'More tools', items: remaining.map((item) => ({ ...item, tone: 'sky' })) })
  const backupTool = lookup.get(role === 'admin' ? '/backup-jobs' : '/backups')
  if (backupTool) {
    for (const group of groups) group.items=group.items.filter(item=>item.to!==backupTool.to)
    const shortcuts = role === 'admin' ? [
      { ...backupTool, to: '/backup-jobs', label: 'Backup manager', icon: Archive, tone: 'sky' },
      { ...backupTool, to: '/backup-jobs?tab=jobs', label: 'Backup jobs', icon: CalendarClock, tone: 'green' },
      { ...backupTool, to: '/backup-jobs?tab=destinations', label: 'Destinations', icon: Cloud, tone: 'violet' },
      { ...backupTool, to: '/backup-jobs?tab=history&view=recovery', label: 'Restore & downloads', icon: RotateCcw, tone: 'amber' },
      { ...backupTool, to: '/backup-jobs?tab=history&view=activity', label: 'Queue & logs', icon: ListChecks, tone: 'teal' },
      { ...backupTool, to: '/backup-jobs?tab=notifications', label: 'Notification plugins', icon: BellRing, tone: 'rose' },
    ] : [
      { ...backupTool, to: '/backups', label: 'Backup manager', icon: Archive, tone: 'sky' },
      { ...backupTool, to: '/backups?component=files', label: 'File backups', icon: FileArchive, tone: 'amber' },
      { ...backupTool, to: '/backups?component=databases', label: 'Database backups', icon: DatabaseBackup, tone: 'violet' },
      { ...backupTool, to: '/backups?component=mail', label: 'Email backups', icon: Mail, tone: 'teal' },
      { ...backupTool, to: '/backups?action=create&kind=full', label: 'Full account backups', icon: HardDriveDownload, tone: 'green' },
      { ...backupTool, to: '/backups?view=restores', label: 'Downloads & activity', icon: Download, tone: 'rose' },
    ]
    groups.push({title:'Backups',items:shortcuts})
  }
  return groups.filter(group=>group.items.length)
}

// Search entries (dashboard search and command palette): every navigation
// item, labelled and sectioned with the same group names as the home screen
// and sidebar so a tool reads the same everywhere.
export function navigationEntries(role, nav, capabilities = []) {
  if (role === 'reseller') {
    let section = 'Reseller'
    return nav.flatMap((item) => {
      if (item.section) { section = item.section; return [] }
      return [{ ...item, section }]
    })
  }
  const sections = new Map()
  for (const group of getToolGroups(role, capabilities)) {
    for (const item of group.items) if (!sections.has(item.to)) sections.set(item.to, { section: group.title, label: item.label })
  }
  return nav.filter((item) => !item.section).map((item) => {
    const known = sections.get(item.to)
    return { ...item, label: known?.label || item.label, section: known?.section || 'General' }
  })
}
