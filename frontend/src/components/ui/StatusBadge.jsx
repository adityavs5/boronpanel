import { cn } from '@/lib/cn'
import { Badge } from './Badge'

// Maps the many status strings the backend emits to a color + dot. Keys are
// matched case-insensitively; unknown values fall back to neutral.
const STATUS_MAP = {
  // account / generic lifecycle
  active: { variant: 'success', dot: 'bg-success' },
  running: { variant: 'info', dot: 'bg-info' },
  allow: { variant: 'success', dot: 'bg-success' },
  allowed: { variant: 'success', dot: 'bg-success' },
  deny: { variant: 'danger', dot: 'bg-danger' },
  denied: { variant: 'danger', dot: 'bg-danger' },
  ban: { variant: 'danger', dot: 'bg-danger' },
  unban: { variant: 'neutral', dot: 'bg-muted-foreground' },
  online: { variant: 'success', dot: 'bg-success' },
  offline: { variant: 'danger', dot: 'bg-danger' },
  degraded: { variant: 'warning', dot: 'bg-warning' },
  unknown: { variant: 'neutral', dot: 'bg-muted-foreground' },
  installed: { variant: 'success', dot: 'bg-success' },
  issued: { variant: 'success', dot: 'bg-success' },
  valid: { variant: 'success', dot: 'bg-success' },
  ok: { variant: 'success', dot: 'bg-success' },
  completed: { variant: 'success', dot: 'bg-success' },
  delivered: { variant: 'success', dot: 'bg-success' },
  enabled: { variant: 'success', dot: 'bg-success' },
  suspended: { variant: 'warning', dot: 'bg-warning' },
  pending: { variant: 'warning', dot: 'bg-warning' },
  provisioning: { variant: 'warning', dot: 'bg-warning' },
  terminating: { variant: 'warning', dot: 'bg-warning' },
  activating: { variant: 'info', dot: 'bg-info' },
  deactivating: { variant: 'info', dot: 'bg-info' },
  queued: { variant: 'warning', dot: 'bg-warning' },
  deferred: { variant: 'warning', dot: 'bg-warning' },
  expiring: { variant: 'warning', dot: 'bg-warning' },
  warning: { variant: 'warning', dot: 'bg-warning' },
  healthy: { variant: 'success', dot: 'bg-success' },
  running_job: { variant: 'info', dot: 'bg-info' },
  terminated: { variant: 'neutral', dot: 'bg-muted-foreground' },
  stopped: { variant: 'neutral', dot: 'bg-muted-foreground' },
  inactive: { variant: 'neutral', dot: 'bg-muted-foreground' },
  disabled: { variant: 'neutral', dot: 'bg-muted-foreground' },
  none: { variant: 'neutral', dot: 'bg-muted-foreground' },
  error: { variant: 'danger', dot: 'bg-danger' },
  failed: { variant: 'danger', dot: 'bg-danger' },
  expired: { variant: 'danger', dot: 'bg-danger' },
  banned: { variant: 'danger', dot: 'bg-danger' },
}

// Sentence case: first letter up, the rest as sent (keeps acronyms such as SSL).
export function sentence(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text
}

export function StatusBadge({ status, label, className }) {
  const key = String(status ?? '').toLowerCase()
  const cfg = STATUS_MAP[key] || { variant: 'neutral', dot: 'bg-muted-foreground' }
  if ((status === null || status === undefined || status === '') && label == null) return <span className="text-muted-foreground">—</span>
  const text = label ?? sentence(String(status ?? '—').replace(/[_-]/g, ' '))
  return (
    <Badge variant={cfg.variant} dot={false} className={className}>
      <span aria-hidden="true" className={cn('h-1.5 w-1.5 shrink-0 rounded-full', cfg.dot)} />
      {text}
    </Badge>
  )
}
