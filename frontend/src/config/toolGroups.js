import { adminNav, customerNav } from './nav'

const customerGroups = {
  evolution: [
    ['Account Manager', ['/domains', '/subdomains', '/ftp', '/ssl', '/databases', '/dns']],
    ['E-mail Manager', ['/email', '/email/settings', '/email/dns']],
    ['WordPress', ['/wordpress']],
    ['Backups', ['/backups']],
    ['Applications & Advanced', ['/node-apps', '/python-apps', '/terminal', '/redis']],
    ['Other Tools', ['/redirects', '/forwarding', '/cache', '/website-security', '/website-maintenance', '/error-pages', '/website-statistics', '/files', '/php', '/cron', '/git', '/ssh', '/disk-usage', '/logs', '/devtools', '/change-password', '/malware', '/security', '/processes', '/appearance']],
  ],
  'paper-lantern': [
    ['Domains', ['/domains', '/subdomains', '/ftp', '/ssl', '/databases', '/dns']],
    ['Email', ['/email', '/email/settings', '/email/dns']],
    ['WordPress', ['/wordpress']],
    ['Backups', ['/backups']],
    ['Software', ['/node-apps', '/python-apps', '/terminal', '/redis']],
    ['Advanced', ['/redirects', '/forwarding', '/cache', '/website-security', '/website-maintenance', '/error-pages', '/website-statistics', '/files', '/php', '/cron', '/git', '/ssh', '/disk-usage', '/logs', '/devtools', '/change-password', '/malware', '/security', '/processes', '/appearance']],
  ],
}
const adminGroups = {
  evolution: [
    ['Account Manager', ['/accounts', '/resellers', '/plans', '/change-password']],
    ['Server Manager', ['/health', '/services', '/openlitespeed', '/db-monitor', '/slow-queries', '/mail-queue', '/cloudflare', '/ip-management']],
    ['Admin Tools', ['/updates', '/ssl', '/backup-jobs', '/malware', '/firewall', '/fail2ban', '/waf', '/ip-bans', '/ip-whitelist', '/tokens']],
    ['System Info & Files', ['/bandwidth', '/site-stats', '/audit-log', '/account-log', '/error-log']],
    ['WordPress & Websites', ['/wordpress', '/templates', '/maintenance-mode']],
    ['Extra Features', ['/import/accounts', '/imap-migrations', '/webhooks', '/notifications']],
    ['Account & Preferences', ['/panel-settings', '/branding', '/appearance', '/security', '/api/docs']],
  ],
  'paper-lantern': [
    ['Accounts', ['/accounts', '/resellers', '/plans', '/ssl', '/import/accounts']],
    ['Server & Databases', ['/health', '/services', '/db-monitor', '/slow-queries']],
    ['Domains & Network', ['/cloudflare', '/ip-management', '/maintenance-mode']],
    ['Email', ['/mail-queue', '/imap-migrations', '/notifications']],
    ['Metrics', ['/bandwidth', '/site-stats', '/audit-log', '/account-log', '/error-log']],
    ['Security', ['/malware', '/firewall', '/fail2ban', '/waf', '/ip-bans', '/ip-whitelist', '/tokens', '/security']],
    ['Software & Advanced', ['/wordpress', '/updates', '/backup-jobs', '/templates', '/webhooks', '/api/docs']],
    ['Preferences', ['/branding', '/change-password', '/appearance']],
  ],
}
const labels = {
  '/files': 'File Manager', '/email': 'Email Accounts', '/ftp': 'FTP Accounts',
  '/email/settings': 'Email Settings', '/email/dns': 'Email DNS Records', '/subdomains': 'Subdomains',
  '/ssl': 'SSL Certificates', '/dns': 'DNS Management', '/php': 'PHP Settings',
  '/git': 'Git Version Control', '/apps': 'Applications', '/node-apps': 'Node.js App', '/python-apps': 'Python App', '/redis': 'Redis',
  '/accounts': 'Manage Accounts', '/health': 'Server Information', '/services': 'Service Monitor',
  '/resellers': 'Reseller Management',
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
  return groups
}
