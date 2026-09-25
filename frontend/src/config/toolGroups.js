import { adminNav, customerNav } from './nav'
import { Archive, CalendarClock, Cloud, DatabaseBackup, Download, FileArchive, HardDriveDownload, ListChecks, Mail, RotateCcw, BellRing } from 'lucide-react'

const customerGroups = {
  evolution: [
    ['Account Manager', ['/domains', '/subdomains', '/ftp', '/ssl', '/databases', '/dns']],
    ['E-mail Manager', ['/email', '/email/settings', '/email/dns', '/email/spam', '/email/migration']],
    ['Software', ['/wordpress', '/node-apps', '/python-apps', '/redis', '/git', '/cron']],
    ['Site Tools', ['/files', '/php', '/redirects', '/forwarding', '/cache', '/website-maintenance', '/error-pages', '/backups']],
    ['Advanced Tools', ['/ssh', '/terminal', '/logs', '/devtools', '/change-password', '/appearance']],
    ['Usage & Security', ['/disk-usage', '/website-statistics', '/processes', '/malware', '/website-security', '/security']],
  ],
  'paper-lantern': [
    ['Domains', ['/domains', '/subdomains', '/ftp', '/ssl', '/databases', '/dns']],
    ['Email', ['/email', '/email/settings', '/email/dns', '/email/spam', '/email/migration']],
    ['Software', ['/wordpress', '/node-apps', '/python-apps', '/redis', '/git', '/cron']],
    ['Site Tools', ['/files', '/php', '/redirects', '/forwarding', '/cache', '/website-maintenance', '/error-pages', '/backups']],
    ['Advanced Tools', ['/ssh', '/terminal', '/logs', '/devtools', '/change-password', '/appearance']],
    ['Usage & Security', ['/disk-usage', '/website-statistics', '/processes', '/malware', '/website-security', '/security']],
  ],
}
const adminGroups = {
  evolution: [
    ['Account Manager', ['/accounts?new=1', '/accounts', '/resellers', '/administrators', '/plans', '/import/accounts']],
    ['Server Manager', ['/health', '/services', '/openlitespeed', '/db-monitor', '/slow-queries', '/dns-setup', '/cloudflare', '/dns-cluster', '/ip-management', '/backup-jobs']],
    ['Email', ['/mail-queue', '/mail-tracking', '/imap-migrations', '/notifications']],
    ['Software & Websites', ['/wordpress', '/software/node-apps', '/software/python-apps', '/templates', '/maintenance-mode', '/updates']],
    ['Security & Network', ['/ssl', '/malware', '/firewall', '/fail2ban', '/waf', '/ip-bans', '/ip-whitelist', '/tokens']],
    ['Metrics, Logs & Integrations', ['/bandwidth', '/site-stats', '/audit-log', '/account-log', '/error-log', '/webhooks']],
    ['Account & Preferences', ['/server-setup', '/panel-settings', '/branding', '/appearance', '/security', '/api/docs']],
  ],
  'paper-lantern': [
    ['Account Manager', ['/accounts?new=1', '/accounts', '/resellers', '/administrators', '/plans', '/import/accounts']],
    ['Server & Databases', ['/health', '/services', '/openlitespeed', '/db-monitor', '/slow-queries', '/backup-jobs', '/updates']],
    ['Domains & Network', ['/dns-setup', '/cloudflare', '/dns-cluster', '/ip-management']],
    ['Email', ['/mail-queue', '/mail-tracking', '/imap-migrations', '/notifications']],
    ['Metrics, Logs & Integrations', ['/bandwidth', '/site-stats', '/audit-log', '/account-log', '/error-log', '/webhooks', '/notifications']],
    ['Security', ['/ssl', '/malware', '/firewall', '/fail2ban', '/waf', '/ip-bans', '/ip-whitelist', '/tokens', '/security']],
    ['Software & Websites', ['/wordpress', '/software/node-apps', '/software/python-apps', '/templates', '/maintenance-mode']],
    ['Developer Tools', ['/api/docs']],
    ['Preferences', ['/server-setup', '/branding', '/change-password', '/appearance']],
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
  const backupTool = lookup.get(role === 'admin' ? '/backup-jobs' : '/backups')
  if (backupTool) {
    for (const group of groups) group.items=group.items.filter(item=>item.to!==backupTool.to)
    const shortcuts = role === 'admin' ? [
      { ...backupTool, to: '/backup-jobs', label: 'Backup Manager', icon: Archive, tone: 'sky' },
      { ...backupTool, to: '/backup-jobs?tab=jobs&action=create', label: 'New Backup Job', icon: CalendarClock, tone: 'green' },
      { ...backupTool, to: '/backup-jobs?tab=destinations&action=create', label: 'Storage Destinations', icon: Cloud, tone: 'violet' },
      { ...backupTool, to: '/backup-jobs?tab=history', label: 'Restore & Downloads', icon: RotateCcw, tone: 'amber' },
      { ...backupTool, to: '/backup-jobs?tab=history', label: 'Queue & Logs', icon: ListChecks, tone: 'teal' },
      { ...backupTool, to: '/backup-jobs?tab=notifications', label: 'Notification Plugins', icon: BellRing, tone: 'rose' },
    ] : [
      { ...backupTool, to: '/backups', label: 'Backup Manager', icon: Archive, tone: 'sky' },
      { ...backupTool, to: '/backups?component=files', label: 'File Backups', icon: FileArchive, tone: 'amber' },
      { ...backupTool, to: '/backups?component=databases', label: 'Database Backups', icon: DatabaseBackup, tone: 'violet' },
      { ...backupTool, to: '/backups?component=mail', label: 'Email Backups', icon: Mail, tone: 'teal' },
      { ...backupTool, to: '/backups?action=create&kind=full', label: 'Full Account Backups', icon: HardDriveDownload, tone: 'green' },
      { ...backupTool, to: '/backups?view=restores', label: 'Downloads & Activity', icon: Download, tone: 'rose' },
    ]
    groups.push({title:'Backups',items:shortcuts})
  }
  return groups.filter(group=>group.items.length)
}
