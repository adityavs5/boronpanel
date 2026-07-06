import { cn } from '@/lib/cn'
import { percent as pct } from '@/lib/utils'

// Usage/progress bar. Auto-colors by fill (accent → warning ≥80% → danger ≥95%)
// unless an explicit `color` is given.
export function ProgressBar({ value, max = 100, color, className, size = 'md', showTrack = true }) {
  const p = max === 100 ? Math.min(100, Math.max(0, value ?? 0)) : pct(value, max)
  const auto = p >= 95 ? 'bg-danger' : p >= 80 ? 'bg-warning' : 'bg-accent'
  const heights = { sm: 'h-1.5', md: 'h-2', lg: 'h-2.5' }
  return (
    <div className={cn('w-full overflow-hidden rounded-full', showTrack && 'bg-muted', heights[size], className)}>
      <div className={cn('h-full rounded-full transition-all duration-500', color || auto)} style={{ width: `${p}%` }} />
    </div>
  )
}

// Usage bar with a label row (used-of-total + percentage).
export function UsageBar({ label, used, total, format = (v) => v, className }) {
  const p = pct(used, total)
  return (
    <div className={cn('space-y-1.5', className)}>
      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium text-foreground tabular-nums">
          {format(used)} <span className="text-muted-foreground">/ {total ? format(total) : '∞'}</span>
        </span>
      </div>
      <ProgressBar value={p} />
    </div>
  )
}
