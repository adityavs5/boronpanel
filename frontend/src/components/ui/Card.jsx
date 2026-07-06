import { cn } from '@/lib/cn'

export function Card({ className, ...props }) {
  return <div className={cn('rounded-card border border-border bg-card shadow-card', className)} {...props} />
}

export function CardHeader({ className, ...props }) {
  return <div className={cn('flex items-start justify-between gap-4 px-6 pt-5 pb-3', className)} {...props} />
}

export function CardTitle({ className, ...props }) {
  return <h3 className={cn('text-base font-semibold text-foreground', className)} {...props} />
}

export function CardDescription({ className, ...props }) {
  return <p className={cn('text-sm text-muted-foreground mt-0.5', className)} {...props} />
}

export function CardContent({ className, ...props }) {
  return <div className={cn('px-6 py-4', className)} {...props} />
}

export function CardFooter({ className, ...props }) {
  return <div className={cn('flex items-center gap-3 px-6 py-4 border-t border-border', className)} {...props} />
}
