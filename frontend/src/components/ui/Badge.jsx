import { cva } from 'class-variance-authority'
import { cn } from '@/lib/cn'

export const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium border',
  {
    variants: {
      variant: {
        neutral: 'bg-muted text-muted-foreground border-transparent',
        accent: 'bg-accent-50 text-accent-700 border-accent-100 dark:bg-accent-950 dark:text-accent-300 dark:border-accent-900',
        success: 'bg-success/10 text-success border-success/20',
        warning: 'bg-warning/10 text-[#B45309] dark:text-warning border-warning/20',
        danger: 'bg-danger/10 text-danger border-danger/20',
        info: 'bg-info/10 text-info border-info/20',
        outline: 'bg-transparent text-foreground border-border',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
)

export function Badge({ className, variant, ...props }) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}
