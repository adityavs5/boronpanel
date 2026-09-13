import { adminNav, customerNav } from './nav'

const customerGroups = {
  evolution: [
    ['Account Manager', ['/domains', '/dns', '/ssl', '/databases', '/ftp', '/change-password']],
    ['E-mail Manager', ['/email']],
    ['WordPress & Websites', ['/wordpress', '/node-apps', '/python-apps']],
    ['Advanced Features', [ '/php', '/redis', '/cron', '/git', '/ssh', '/devtools']],
    ['System Info & Files', ['/files', '/backups', '/disk-usage', '/processes', '/logs', '/terminal']],
    ['Account & Preferences', ['/security', '/appearance']],
  ],
  'paper-lantern': [
    ['Files', ['/files', '/disk-usage', '/ftp', '/backups', '/git']],
    ['Databases', ['/databases', '/redis']],
    ['Domains', ['/domains', '/dns']],
    ['Email', ['/email']],
    ['Metrics', ['/logs', '/processes']],
    ['Security', ['/ssl', '/ssh', '/security']],
    ['Software', ['/wordpress', '/node-apps', '/python-apps', '/php', '/devtools']],
    ['Advanced', ['/cron', '/terminal']],
    ['Preferences', ['/change-password', '/appearance']],
  ],
}
const adminGroups = {
  evolution: [
    ['Account Manager', ['/backup-jobs', '/accounts', '/plans', '/bandwidth', '/change-password']],
    ['Server Manager', ['/health', '/services', '/db-monitor', '/slow-queries', '/mail-queue', '/cloudflare']],
    ['Admin Tools', ['/updates', '/firewall', '/fail2ban', '/waf', '/ip-bans', '/ip-whitelist', '/tokens']],
    ['System Info & Files', ['/site-stats', '/audit-log', '/account-log', '/error-log', '/templates', '/maintenance-mode']],
    ['WordPress & Websites', ['/wordpress']],
    ['Extra Features', ['/import/cpanel', '/imap-migrations', '/webhooks', '/notifications']],
    ['Account & Preferences', ['/panel-settings', '/branding', '/appearance', '/security', '/api/docs']],
  ],
  'paper-lantern': [
    ['Accounts', ['/backup-jobs', '/accounts', '/plans', '/import/cpanel']],
    ['Server & Databases', ['/health', '/services', '/db-monitor', '/slow-queries']],
    ['Domains & Network', ['/cloudflare', '/maintenance-mode']],
    ['Email', ['/mail-queue', '/imap-migrations', '/notifications']],
    ['Metrics', ['/bandwidth', '/site-stats', '/audit-log', '/account-log', '/error-log']],
    ['Security', ['/firewall', '/fail2ban', '/waf', '/ip-bans', '/ip-whitelist', '/tokens', '/security']],
    ['Software & Advanced', ['/wordpress', '/updates', '/templates', '/webhooks', '/api/docs']],
    ['Preferences', ['/branding', '/change-password', '/appearance']],
  ],
}
const labels = {
  '/files': 'File Manager', '/email': 'Email Accounts', '/ftp': 'FTP Accounts',
  '/ssl': 'SSL Certificates', '/dns': 'DNS Management', '/php': 'PHP Settings',
  '/git': 'Git Version Control', '/apps': 'Applications', '/node-apps': 'Node.js App', '/python-apps': 'Python App', '/redis': 'Redis',
  '/accounts': 'Manage Accounts', '/health': 'Server Information', '/services': 'Service Monitor',
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
