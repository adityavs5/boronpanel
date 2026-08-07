import { cn } from '@/lib/cn'

// Standard page header: title + optional description on the left, actions on
// the right. Used at the top of every page.
export function PageHeader({ title, description, children, className, icon: Icon }) {
  return (
    <div className={cn('flex flex-wrap items-start justify-between gap-4 mb-6', className)}>
      <div className="flex items-start gap-3 min-w-0">
        {Icon && (
          <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-btn bg-accent-50 text-accent-600 dark:bg-accent-950 dark:text-accent-300">
            <Icon className="h-5 w-5" />
          </div>
        )}
        <div className="min-w-0">
          <h1 className="break-words text-xl font-semibold text-foreground">{title}</h1>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        </div>
      </div>
      {children && <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:shrink-0">{children}</div>}
    </div>
  )
}
