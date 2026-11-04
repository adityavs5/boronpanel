import { useLayoutEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useBranding } from '@/hooks/useBranding'
import { activeTheme } from '@/themes/runtime'
import { toolIcon } from '@/config/toolIcons'
import { cn } from '@/lib/cn'
import { MoreHorizontal } from 'lucide-react'
import { Button } from './Button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from './DropdownMenu'

// Standard page header: title + optional description on the left, actions on
// the right. Used at the top of every page.
export function PageHeader({ title, titleMeta, description, primaryAction, moreActions = [], children, className, icon: Icon }) {
  const { panelName } = useBranding()
  const { pathname, search } = useLocation()
  const tools = activeTheme()?.toolIcons
  // A theme's own pictogram for this tool wins, so every page carries its icon.
  const Glyph = tools?.[pathname + search] || tools?.[pathname] || Icon || toolIcon(pathname)
  useLayoutEffect(() => {
    document.documentElement.dataset.pageTitle = title
    if (panelName) document.title = `${title} · ${panelName}`
    return () => {
      if (document.documentElement.dataset.pageTitle === title) delete document.documentElement.dataset.pageTitle
    }
  }, [title, panelName])
  return (
    <div className={cn('panel-page-heading flex flex-wrap items-start justify-between gap-4', className)}>
      <div className="flex items-start gap-3 min-w-0">
        {Glyph && (
          <div className="page-heading-icon" aria-hidden="true">
            <Glyph />
          </div>
        )}
        <div className="min-w-0">
          <div className="page-heading-title-line">
            <h1 className="break-words">{title}</h1>
            {titleMeta}
          </div>
          {description && <p>{description}</p>}
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
