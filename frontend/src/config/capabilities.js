const PREFIX_CAPABILITY = [
  ['/email', 'mail'], ['/mail-', 'mail'], ['/imap-', 'mail'],
  ['/dns', 'dns'], ['/cloudflare', 'dns'], ['/nameservers', 'dns'],
  ['/databases', 'database'], ['/db-monitor', 'database'], ['/slow-queries', 'database'],
  ['/ftp', 'ftp'], ['/redis', 'redis'], ['/node-', 'apps'], ['/python-', 'apps'],
  ['/software/node-', 'apps'], ['/software/python-', 'apps'],
  ['/wordpress', 'wordpress'], ['/backup', 'backups'], ['/waf', 'waf'],
  ['/malware', 'malware'], ['/fail2ban', 'fail2ban'],
]

export function capabilityForPath(path = '') {
  return PREFIX_CAPABILITY.find(([prefix]) => path.startsWith(prefix))?.[1] || null
}

export function pathIsAvailable(path, capabilities = []) {
  if (!capabilities?.length) return true
  const required = capabilityForPath(path)
  return !required || capabilities.includes(required)
}

export function filterNavigation(items, capabilities = []) {
  const filtered = items.filter(item => item.section || pathIsAvailable(item.to, capabilities))
  return filtered.filter((item, index) => !item.section || filtered.slice(index + 1).some(next => !next.section))
}
