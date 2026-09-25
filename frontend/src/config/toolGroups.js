import { adminNav, customerNav } from './nav'
import { Archive, CalendarClock, Cloud, Database, FolderArchive, History, Mail, RotateCcw } from 'lucide-react'

const customerGroups = {
  evolution: [
    ['Account Manager', ['/domains', '/subdomains', '/ftp', '/ssl', '/databases', '/dns']],
    ['E-mail Manager', ['/email', '/email/settings', '/email/dns', '/email/spam', '/email/migration']],
    ['Software', ['/wordpress', '/node-apps', '/python-apps', '/redis']],
    ['Backups', ['/backups']],
    ['Site Tools', ['/files', '/php', '/redirects', '/forwarding', '/cache', '/website-maintenance', '/error-pages']],
    ['Developer Tools', ['/git', '/cron', '/ssh', '/terminal', '/logs', '/devtools']],
    ['Usage & Security', ['/disk-usage', '/website-statistics', '/processes', '/malware', '/website-security', '/security', '/change-password', '/appearance']],
  ],
  'paper-lantern': [
    ['Domains', ['/domains', '/subdomains', '/ftp', '/ssl', '/databases', '/dns']],
    ['Email', ['/email', '/email/settings', '/email/dns', '/email/spam', '/email/migration']],
    ['Software', ['/wordpress', '/node-apps', '/python-apps', '/redis']],
    ['Backups', ['/backups']],
    ['Site Tools', ['/files', '/php', '/redirects', '/forwarding', '/cache', '/website-maintenance', '/error-pages']],
    ['Developer Tools', ['/git', '/cron', '/ssh', '/terminal', '/logs', '/devtools']],
    ['Usage & Security', ['/disk-usage', '/website-statistics', '/processes', '/malware', '/website-security', '/security', '/change-password', '/appearance']],
  ],
}
const adminGroups = {
  evolution: [
    ['Account Manager', ['/accounts?new=1', '/accounts', '/resellers', '/administrators', '/plans']],
    ['Server Manager', ['/health', '/services', '/openlitespeed', '/db-monitor', '/slow-queries', '/cloudflare', '/dns-cluster', '/ip-management']],
    ['Email', ['/mail-queue', '/mail-tracking', '/imap-migrations', '/notifications']],
    ['Software & Websites', ['/wordpress', '/software/node-apps', '/software/python-apps', '/templates', '/maintenance-mode']],
    ['Backups & Updates', ['/backup-jobs', '/updates', '/import/accounts']],
    ['Security & Network', ['/ssl', '/malware', '/firewall', '/fail2ban', '/waf', '/ip-bans', '/ip-whitelist', '/tokens']],
    ['Metrics, Logs & Integrations', ['/bandwidth', '/site-stats', '/audit-log', '/account-log', '/error-log', '/webhooks', '/notifications']],
    ['Account & Preferences', ['/panel-settings', '/branding', '/appearance', '/security', '/api/docs']],
  ],
  'paper-lantern': [
    ['Account Manager', ['/accounts?new=1', '/accounts', '/resellers', '/administrators', '/plans']],
    ['Server & Databases', ['/health', '/services', '/openlitespeed', '/db-monitor', '/slow-queries']],
    ['Domains & Network', ['/cloudflare', '/dns-cluster', '/ip-management']],
    ['Email', ['/mail-queue', '/mail-tracking', '/imap-migrations', '/notifications']],
    ['Metrics, Logs & Integrations', ['/bandwidth', '/site-stats', '/audit-log', '/account-log', '/error-log', '/webhooks', '/notifications']],
    ['Security', ['/ssl', '/malware', '/firewall', '/fail2ban', '/waf', '/ip-bans', '/ip-whitelist', '/tokens', '/security']],
    ['Software & Websites', ['/wordpress', '/software/node-apps', '/software/python-apps', '/templates', '/maintenance-mode']],
    ['Backups & Updates', ['/backup-jobs', '/updates', '/import/accounts']],
    ['Developer Tools', ['/api/docs']],
    ['Preferences', ['/branding', '/change-password', '/appearance']],
  ],
}
const labels = {
  '/files': 'File Manager', '/email': 'Email Accounts', '/ftp': 'FTP Accounts',
  '/email/settings': 'Email Settings', '/email/spam': 'Spam Filters', '/email/migration': 'IMAP Migration', '/email/dns': 'Email DNS Records', '/subdomains': 'Subdomains',
  '/ssl': 'SSL Certificates', '/dns': 'DNS Management', '/php': 'PHP Settings',
  '/git': 'Git Version Control', '/apps': 'Applications', '/node-apps': 'Node.js App', '/python-apps': 'Python App', '/redis': 'Redis',
  '/accounts': 'Manage Accounts', '/health': 'Server Information', '/services': 'Service Monitor',
  '/resellers': 'Reseller Management',
  '/accounts?new=1': 'Add New User', '/administrators': 'Administrators',
  '/software/node-apps': 'Node.js Applications', '/software/python-apps': 'Python Applications',
  '/updates': 'Panel Updates', '/appearance': 'Change Style',
}
const tones = ['sky', 'green', 'amber', 'violet', 'rose', 'teal']

// Look up links in the canonical role-aware navigation rather than creating
// synthetic tools that lead nowhere. New navigation entries get a fallback group.
export function getToolGroups(role, skin) {
  const nav = role === 'admin' ? adminNav : customerNav
  const lookup = new Map(nav.filter((item) => item.to).map((item) => [item.to, item]))
  const layouts = role === 'admin' ? adminGroups : customerGroups
  const definitions = layouts[skin] || layouts.evolution
  const used = new Set(['/overview', '/dashboard'])
  const groups = definitions.map(([title, paths], index) => ({
    title,
    items: paths.filter((path) => lookup.has(path)).map((path, i) => {
      used.add(path)
      return { ...lookup.get(path), label: labels[path] || lookup.get(path).label, tone: tones[(index + i) % tones.length] }
    }),
  })).filter((group) => group.items.length)
  const remaining = [...lookup.values()].filter((item) => !used.has(item.to))
  if (remaining.length) groups.push({ title: 'More tools', items: remaining.map((item) => ({ ...item, tone: 'sky' })) })
  const backupGroup = groups.find((group) => group.title.startsWith('Backups'))
  const backupTool = lookup.get(role === 'admin' ? '/backup-jobs' : '/backups')
  if (backupGroup && backupTool) {
    backupGroup.items = role === 'admin' ? [
      { ...backupTool, to: '/backup-jobs', label: 'Backup Manager', icon: Archive, tone: 'sky' },
      { ...backupTool, to: '/backup-jobs?tab=jobs&action=create', label: 'New Backup Job', icon: CalendarClock, tone: 'green' },
      { ...backupTool, to: '/backup-jobs?tab=destinations&action=create', label: 'Storage Destinations', icon: Cloud, tone: 'violet' },
      { ...backupTool, to: '/backup-jobs?tab=history', label: 'Run History', icon: History, tone: 'amber' },
      ...backupGroup.items.filter((item) => item.to !== '/backup-jobs'),
    ] : [
      { ...backupTool, to: '/backups', label: 'Backup Overview', icon: Archive, tone: 'sky' },
      { ...backupTool, to: '/backups?action=create&kind=full', label: 'Full Account Backup', icon: FolderArchive, tone: 'green' },
      { ...backupTool, to: '/backups?action=create&kind=file', label: 'Files Backup', icon: FolderArchive, tone: 'amber' },
      { ...backupTool, to: '/backups?action=create&kind=database', label: 'Database Backup', icon: Database, tone: 'violet' },
      { ...backupTool, to: '/backups?action=create&kind=databases', label: 'All Databases', icon: Database, tone: 'teal' },
      { ...backupTool, to: '/backups?action=create&kind=mailbox', label: 'Mailbox Backup', icon: Mail, tone: 'rose' },
      { ...backupTool, to: '/backups?view=restores', label: 'Restore History', icon: RotateCcw, tone: 'sky' },
    ]
  }
  // Backups are a deliberate final destination on both dashboards.  Keep the
  // group last even when newly added navigation entries fall into More tools.
  const backups = groups.findIndex((group) => group.title.startsWith('Backups'))
  if (backups >= 0) groups.push(groups.splice(backups, 1)[0])
  return groups
}
