import { AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/cn'

// The reason a form that stays open was refused, shown as the server wrote
// it (messages can start with a record or path name, so they are never
// re-cased). A toast disappears after a few seconds; this stays beside the
// fields until the next attempt.
export function InlineError({ error, className, children }) {
  const text = error instanceof Error ? error.message : error
  if (!text && !children) return null
  return (
    <div role="alert" className={cn('break-words rounded-control border border-danger/35 bg-danger/10 px-3 py-2 text-sm text-danger', className)}>
      {children || text}
    </div>
  )
}

// A completed action with something the customer should know about.
export function InlineWarning({ title, children, className }) {
  return (
    <div role="status" className={cn('flex items-start gap-2 break-words rounded-btn border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-foreground', className)}>
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden="true" />
      <div className="min-w-0 space-y-1">
        {title && <p className="font-medium">{title}</p>}
        {children}
      </div>
    </div>
  )
}
