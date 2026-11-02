import { Children, isValidElement } from 'react'
import { cva } from 'class-variance-authority'
import { cn } from '@/lib/cn'

export const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium leading-5 border',
  {
    variants: {
      variant: {
        neutral: 'bg-muted text-muted-foreground border-transparent',
        accent: 'bg-accent-soft text-link border-transparent',
        success: 'bg-success/10 text-success border-transparent',
        warning: 'bg-warning/10 text-warning border-transparent',
        danger: 'bg-danger/10 text-danger border-transparent',
        info: 'bg-info/10 text-info border-transparent',
        // Statuses use the soft-tint pill; outline is kept as a neutral alias.
        outline: 'bg-muted text-muted-foreground border-transparent',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
)

const DOTS = { success: 'bg-success', warning: 'bg-warning', danger: 'bg-danger', info: 'bg-info' }

// Semantic badges are statuses (DESIGN.md: soft tint plus a coloured dot).
// Pass dot={false} for counts or tags that use a semantic colour.
export function Badge({ className, variant, dot, children, ...props }) {
  // A badge that already leads with an icon component does not also get a dot.
  const hasIcon = Children.toArray(children).some((child) => isValidElement(child) && typeof child.type !== 'string')
  const showDot = dot ?? (!!DOTS[variant] && !hasIcon)
  return <span data-ui-badge={variant || 'neutral'} className={cn(badgeVariants({ variant }), className)} {...props}>
    {showDot && <span aria-hidden="true" className={cn('h-1.5 w-1.5 shrink-0 rounded-full', DOTS[variant] || 'bg-muted-foreground')} />}
    {children}
  </span>
}
