import { AlertTriangle, Inbox, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/cn'
import { Button } from './Button'

// Empty state with an optional call-to-action. Used by every list/table.
export function EmptyState({ icon: Icon = Inbox, title, description, action, className }) {
  return (
    <div data-ui-state="empty" className={cn('ui-state flex flex-col items-center justify-center gap-3 px-6 py-14 text-center', className)}>
      <div className="flex h-12 w-12 items-center justify-center rounded-chip bg-accent-soft text-link">
        <Icon className="h-6 w-6" strokeWidth={1.75} />
      </div>
      <div className="space-y-1">
        <h3 className="text-[15px] font-semibold text-foreground">{title}</h3>
        {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  )
}

// Error state with a retry button. `error` may be an Error or string.
export function ErrorState({ error, onRetry, className, title = 'Something went wrong' }) {
  // Prefer the server's own explanation (FastAPI `detail`) over the generic message.
  const detail = error?.response?.data?.detail ?? error?.data?.detail ?? error?.detail
  const message = typeof error === 'string' ? error : (typeof detail === 'string' && detail) || error?.message || 'Unexpected error.'
  return (
    <div data-ui-state="error" role="alert" className={cn('ui-state flex flex-col items-center justify-center gap-3 px-6 py-14 text-center', className)}>
      <div className="flex h-12 w-12 items-center justify-center rounded-chip bg-danger/10 text-danger">
        <AlertTriangle className="h-6 w-6" strokeWidth={1.75} />
      </div>
      <div className="space-y-1">
        <h3 className="text-[15px] font-semibold text-foreground">{title}</h3>
        <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      </div>
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          <RefreshCw className="h-4 w-4" /> Retry
        </Button>
      )}
    </div>
  )
}
