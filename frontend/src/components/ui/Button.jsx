import { forwardRef } from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva } from 'class-variance-authority'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/cn'

export const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-btn text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/70 focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 select-none',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-accent-foreground hover:bg-accent-600 active:bg-accent-700 shadow-sm',
        secondary: 'bg-surface text-foreground border border-border hover:bg-muted active:bg-muted/80',
        outline: 'border border-border bg-transparent text-foreground hover:bg-muted active:bg-muted/80',
        ghost: 'bg-transparent text-foreground hover:bg-muted active:bg-muted/80',
        danger: 'bg-danger text-danger-foreground hover:bg-danger/90 active:bg-danger/80 shadow-sm',
        success: 'bg-success text-success-foreground hover:bg-success/90 active:bg-success/80 shadow-sm',
        warning: 'bg-warning text-warning-foreground hover:bg-warning/90 active:bg-warning/80 shadow-sm',
        link: 'bg-transparent text-accent-600 dark:text-accent-400 underline-offset-4 hover:underline p-0 h-auto',
      },
      size: {
        sm: 'h-8 px-3 text-xs',
        md: 'h-9 px-4',
        lg: 'h-10 px-6',
        icon: 'h-9 w-9 p-0',
        'icon-sm': 'h-8 w-8 p-0',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
)

export const Button = forwardRef(function Button(
  { className, variant, size, asChild = false, loading = false, disabled, children, ...props },
  ref,
) {
  const Comp = asChild ? Slot : 'button'
  return (
    <Comp
      ref={ref}
      className={cn(buttonVariants({ variant, size, className }))}
      disabled={disabled || loading}
      {...props}
    >
      {/* Slot (radix-slot >=1.3) requires exactly ONE child — even a falsy
          `loading && …` sibling makes it throw. asChild callers render their
          child untouched; the loader only applies to real <button>s. */}
      {asChild ? (
        children
      ) : (
        <>
          {loading && <Loader2 className="h-4 w-4 animate-spin" />}
          {children}
        </>
      )}
    </Comp>
  )
})
