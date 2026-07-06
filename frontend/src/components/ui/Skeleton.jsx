import { cn } from '@/lib/cn'

export function Skeleton({ className, ...props }) {
  return <div className={cn('shimmer rounded-md bg-muted', className)} {...props} />
}

// A ready-made skeleton for a table body while data loads.
export function TableSkeleton({ rows = 5, cols = 4 }) {
  return (
    <div className="divide-y divide-border">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-4 px-4 py-3">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className={cn('h-4', c === 0 ? 'w-1/4' : 'flex-1')} />
          ))}
        </div>
      ))}
    </div>
  )
}

export function CardSkeleton({ className }) {
  return (
    <div className={cn('rounded-card border border-border bg-card p-6 space-y-3', className)}>
      <Skeleton className="h-5 w-1/3" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-2/3" />
    </div>
  )
}
