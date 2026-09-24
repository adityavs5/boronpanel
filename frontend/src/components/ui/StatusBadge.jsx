import { cn } from '@/lib/cn'
import { Badge } from './Badge'

// Maps the many status strings the backend emits to a color + dot. Keys are
// matched case-insensitively; unknown values fall back to neutral.
const STATUS_MAP = {
  // account / generic lifecycle
  active: { variant: 'success', dot: 'bg-success' },
  running: { variant: 'success', dot: 'bg-success' },
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
  queued: { variant: 'warning', dot: 'bg-warning' },
  deferred: { variant: 'warning', dot: 'bg-warning' },
  expiring: { variant: 'warning', dot: 'bg-warning' },
  warning: { variant: 'warning', dot: 'bg-warning' },
  healthy: { variant: 'success', dot: 'bg-success' },
  running_job: { variant: 'info', dot: 'bg-info' },
  terminated: { variant: 'neutral', dot: 'bg-gray-400' },
  stopped: { variant: 'neutral', dot: 'bg-gray-400' },
  inactive: { variant: 'neutral', dot: 'bg-gray-400' },
  disabled: { variant: 'neutral', dot: 'bg-gray-400' },
  none: { variant: 'neutral', dot: 'bg-gray-400' },
  error: { variant: 'danger', dot: 'bg-danger' },
  failed: { variant: 'danger', dot: 'bg-danger' },
  expired: { variant: 'danger', dot: 'bg-danger' },
  banned: { variant: 'danger', dot: 'bg-danger' },
}

export function StatusBadge({ status, label, className }) {
  const key = String(status ?? '').toLowerCase()
  const cfg = STATUS_MAP[key] || { variant: 'neutral', dot: 'bg-gray-400' }
  return (
    <Badge variant={cfg.variant} className={cn('capitalize', className)}>
      <span className={cn('h-1.5 w-1.5 rounded-full', cfg.dot)} />
      {label ?? String(status ?? '—').replace(/_/g, ' ')}
    </Badge>
  )
}
