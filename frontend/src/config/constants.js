// Injected by vite.config.js from version.py (the repo-wide single source of
// truth) at build time; the fallback only exists for tooling that evaluates
// this module outside a Vite build.
export const APP_VERSION =
  typeof __BORON_VERSION__ !== 'undefined' ? `v${__BORON_VERSION__}` : 'v0.0.0-dev'

// PHP versions offered in switchers. Must match shared/config.py's
// php_versions (the backend rejects anything else) -- this list previously
// offered uninstalled 8.0/7.4 (guaranteed server-side error) and omitted
// installed 8.4/8.5.
export const PHP_VERSIONS = ['8.5', '8.4', '8.3', '8.2', '8.1']

export const NODE_VERSIONS = ['20', '18']

// Notification / webhook event catalogs (labels for the settings UIs).
export const NOTIFICATION_EVENTS = [
  { key: 'account.created', label: 'Account created' },
  { key: 'account.suspended', label: 'Account suspended' },
  { key: 'account.unsuspended', label: 'Account unsuspended' },
  { key: 'account.terminated', label: 'Account terminated' },
  { key: 'backup.completed', label: 'Backup completed' },
  { key: 'backup.failed', label: 'Backup failed' },
  { key: 'backup.partial', label: 'Backup completed with warnings' },
  { key: 'backup.overdue', label: 'Backup overdue' },
  { key: 'backup.destination_unavailable', label: 'Backup destination unavailable' },
  { key: 'backup.restore_completed', label: 'Restore completed' },
  { key: 'backup.restore_failed', label: 'Restore failed' },
  { key: 'backup.download_ready', label: 'Download ready' },
  { key: 'ssl.expiring', label: 'SSL certificate expiring' },
  { key: 'usage.limit.reached', label: 'Resource usage alert' },
  { key: 'login.new', label: 'New panel login' },
  { key: 'dns.zone_activated', label: 'DNS zone activated' },
  { key: 'task.failed', label: 'Critical task failed' },
  { key: 'host.disk', label: 'Host disk space low' },
  { key: 'host.inodes', label: 'Host inode space low' },
  { key: 'host.memory', label: 'Host memory pressure' },
  { key: 'host.load', label: 'Host load pressure' },
  { key: 'host.login_burst', label: 'Failed-login burst' },
  { key: 'host.service_down', label: 'Host service down' },
  { key: 'host.service_recovered', label: 'Host service recovered' },
]

export const WEBHOOK_EVENTS = [
  'account.created',
  'account.suspended',
  'account.terminated',
  'backup.completed',
  'backup.failed',
  'backup.partial',
  'backup.overdue',
  'backup.destination_unavailable',
  'backup.restore_completed',
  'backup.restore_failed',
  'backup.download_ready',
  'ssl.expiring',
  'usage.limit.reached',
  'dns.zone_activated',
  'task.failed',
  'host.disk',
  'host.inodes',
  'host.memory',
  'host.load',
  'host.login_burst',
  'host.service_down',
  'host.service_recovered',
]
