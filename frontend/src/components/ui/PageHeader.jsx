import { useLayoutEffect } from 'react'
import { useBranding } from '@/hooks/useBranding'
import { cn } from '@/lib/cn'
import { MoreHorizontal } from 'lucide-react'
import { Button } from './Button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from './DropdownMenu'

// Standard page header: title + optional description on the left, actions on
// the right. Used at the top of every page.
export function PageHeader({ title, titleMeta, description, primaryAction, moreActions = [], children, className, icon: Icon }) {
  const { panelName } = useBranding()
  useLayoutEffect(() => {
    document.documentElement.dataset.pageTitle = title
    if (panelName) document.title = `${title} · ${panelName}`
    return () => {
      if (document.documentElement.dataset.pageTitle === title) delete document.documentElement.dataset.pageTitle
    }
  }, [title, panelName])
  return (
    <div className={cn('panel-page-heading flex flex-wrap items-start justify-between gap-4 mb-6', className)}>
      <div className="flex items-start gap-3 min-w-0">
        {Icon && (
          <div className="page-heading-icon mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-btn bg-accent-50 text-accent-600 dark:bg-accent-950 dark:text-accent-300">
            <Icon className="h-5 w-5" />
          </div>
        )}
        <div className="min-w-0">
          <div className="page-heading-title-line">
            <h1 className="break-words text-xl font-semibold text-foreground">{title}</h1>
            {titleMeta}
          </div>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        </div>
      </div>
      {(children || primaryAction || moreActions.length > 0) && <div className="page-heading-actions flex w-full flex-wrap items-center gap-2 sm:w-auto sm:shrink-0">
        {children}
        {primaryAction}
        {moreActions.length > 0 && <DropdownMenu>
          <DropdownMenuTrigger asChild><Button variant="secondary"><MoreHorizontal className="h-4 w-4" />More</Button></DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {moreActions.map((action, index) => action.separator ? <DropdownMenuSeparator key={`separator-${index}`} /> : <DropdownMenuItem key={action.label} disabled={action.disabled} destructive={action.destructive} onSelect={action.onSelect}>{action.icon}{action.label}</DropdownMenuItem>)}
          </DropdownMenuContent>
        </DropdownMenu>}
      </div>}
    </div>
  )
}
