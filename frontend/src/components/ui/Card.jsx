import { cn } from '@/lib/cn'

export function Card({ className, ...props }) {
  return <div className={cn('panel-card rounded-card border border-border bg-card shadow-card', className)} {...props} />
}

export function CardHeader({ className, ...props }) {
  return <div className={cn('panel-card-header flex items-center justify-between gap-4', className)} {...props} />
}

export function CardTitle({ className, ...props }) {
  return <h3 className={cn('text-[15px] font-semibold text-heading', className)} {...props} />
}

export function CardDescription({ className, ...props }) {
  return <p className={cn('panel-card-description text-sm text-muted-foreground mt-0.5', className)} {...props} />
}

export function CardContent({ className, ...props }) {
  return <div className={cn('panel-card-content', className)} {...props} />
}

export function CardFooter({ className, ...props }) {
  return <div className={cn('panel-card-footer flex items-center gap-3', className)} {...props} />
}
