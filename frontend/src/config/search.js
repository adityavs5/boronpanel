// One searchable vocabulary for dashboard tools and the keyboard palette.
const synonyms = {
  '/node-apps': 'node nodejs node.js javascript application apps express npm runtime',
  '/python-apps': 'python application apps django flask fastapi wsgi asgi pip runtime',
  '/wordpress': 'wordpress wp blog website cms softaculous installer install clone staging backup login plugins themes',
  '/dns': 'dns zone editor zones records nameserver nameservers a aaaa cname mx txt spf dkim domain name management',
  '/domains': 'domain subdomain addon parked alias website redirect',
  '/email': 'email mail mailbox webmail forwarder forwarding autoresponder autoresponders vacation smtp imap inbox',
  '/databases': 'database mysql mariadb sql phpmyadmin db users',
  '/files': 'file manager upload download folders permissions documents',
  '/ssl': 'ssl tls https certificate lets encrypt security',
  '/backups': 'backup restore recovery snapshots archive',
  '/php': 'php version extensions settings configuration ini memory upload limit',
  '/ftp': 'ftp sftp file transfer accounts', '/cron': 'cron scheduled task jobs automation',
  '/security': 'security two factor 2fa totp authenticator', '/appearance': 'theme style skin appearance evolution paper lantern dark light',
  '/accounts': 'hosting users customers accounts domains management', '/updates': 'panel upgrade update version release',
  '/logs': 'logs errors access debug troubleshooting', '/disk-usage': 'disk storage space quota usage',
  '/ssh': 'ssh keys secure shell', '/devtools': 'developer composer wpcli tools',
}
export function normalize(text) { return String(text || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim() }
function distance(a, b) {
  let row = Array.from({ length: b.length + 1 }, (_, i) => i)
  for (let i = 1; i <= a.length; i++) { const next = [i]; for (let j = 1; j <= b.length; j++) next[j] = Math.min(next[j - 1] + 1, row[j] + 1, row[j - 1] + (a[i - 1] !== b[j - 1])); row = next }
  return row[b.length]
}
export function searchScore(item, query) {
  const q = normalize(query); if (!q) return 1
  const label = normalize(item.label)
  const text = normalize(`${item.label} ${item.section || item.group || ''} ${item.to || ''} ${synonyms[item.to] || ''}`)
  const words = text.split(' ')
  const tokens = q.split(' ').filter((word) => !['the', 'a', 'to', 'my', 'how', 'do', 'i', 'settings', 'manage', 'can', 'you', 'add', 'create', 'set', 'up', 'change', 'view', 'show', 'me', 'edit', 'configure', 'for', 'an'].includes(word))
  if (!tokens.length) return text.includes(q) ? 1 : 0
  let score = 0
  for (const token of tokens) {
    if (words.includes(token)) score += 8
    else if (words.some((w) => w.startsWith(token))) score += 5
    else if (token.length >= 3 && words.some((w) => Math.abs(w.length - token.length) <= 2 && distance(w, token) <= (token.length > 6 ? 2 : 1))) score += 2
    else return 0
  }
  return score + (label.includes(q) ? 20 : 0)
}
export function searchEntries(entries, query) { return entries.map((item, index) => ({ item, index, score: searchScore(item, query) })).filter((r) => r.score > 0).sort((a, b) => b.score - a.score || a.index - b.index).map((r) => r.item) }
