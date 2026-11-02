import { cn } from '@/lib/cn'

// Shared stat card: icon chip, sentence-case label, then the value.
export function StatCard({ icon: Icon, label, value, className }) {
  return (
    <div className={cn('stat-card', className)}>
      {Icon && <span className="stat-card-chip" aria-hidden="true"><Icon /></span>}
      <div className="min-w-0">
        <div className="stat-card-label">{label}</div>
        <div className="stat-card-value">{value}</div>
      </div>
    </div>
  )
}

export function StatGrid({ className, ...props }) {
  return <div className={cn('stat-grid', className)} {...props} />
}
