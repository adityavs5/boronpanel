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
  { key: 'account_created', label: 'Account created' },
  { key: 'account_suspended', label: 'Account suspended' },
  { key: 'account_unsuspended', label: 'Account unsuspended' },
  { key: 'account_terminated', label: 'Account terminated' },
  { key: 'backup_completed', label: 'Backup completed' },
  { key: 'backup_failed', label: 'Backup failed' },
  { key: 'ssl_expiring', label: 'SSL certificate expiring' },
  { key: 'usage_80', label: 'Usage at 80%' },
  { key: 'usage_90', label: 'Usage at 90%' },
  { key: 'usage_100', label: 'Usage at 100%' },
  { key: 'new_login', label: 'New panel login' },
]

export const WEBHOOK_EVENTS = [
  'account.created',
  'account.suspended',
  'account.terminated',
  'backup.completed',
  'ssl.expiring',
  'usage.limit.reached',
]
