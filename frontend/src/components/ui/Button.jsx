import { forwardRef } from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva } from 'class-variance-authority'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/cn'

export const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-btn text-sm font-medium transition-[background-color,border-color,color,box-shadow] duration-150 ease-out focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/30 disabled:pointer-events-none disabled:opacity-60 select-none [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-accent-foreground hover:bg-accent-hover active:bg-accent-hover disabled:bg-muted disabled:text-muted-foreground disabled:opacity-100',
        secondary: 'bg-surface text-foreground border border-input hover:bg-muted active:bg-muted',
        outline: 'bg-surface text-foreground border border-input hover:bg-muted active:bg-muted',
        ghost: 'bg-transparent text-foreground hover:bg-muted active:bg-muted',
        danger: 'bg-danger-solid text-danger-foreground hover:bg-danger-solid/90 active:bg-danger-solid/80 disabled:bg-muted disabled:text-muted-foreground disabled:opacity-100',
        // Quiet destructive action for table rows: danger text, no fill.
        'danger-ghost': 'border border-input bg-surface text-danger hover:bg-danger/10 hover:border-danger/40 active:bg-danger/15',
        // DESIGN.md allows primary, secondary, ghost and danger only. The old
        // success/warning fills are kept as aliases so call sites keep working.
        success: 'bg-accent text-accent-foreground hover:bg-accent-hover active:bg-accent-hover disabled:bg-muted disabled:text-muted-foreground disabled:opacity-100',
        warning: 'bg-accent text-accent-foreground hover:bg-accent-hover active:bg-accent-hover disabled:bg-muted disabled:text-muted-foreground disabled:opacity-100',
        link: 'bg-transparent text-link underline-offset-4 hover:underline p-0 h-auto',
      },
      size: {
        sm: 'h-[var(--control-h-sm)] px-3 text-[13px]',
        md: 'h-[var(--control-h)] px-4',
        lg: 'h-[var(--control-h-lg)] px-5',
        icon: 'h-[var(--control-h)] w-[var(--control-h)] p-0',
        'icon-sm': 'h-[var(--control-h-sm)] w-[var(--control-h-sm)] p-0',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
)

export const Button = forwardRef(function Button(
  { className, variant, size, asChild = false, loading = false, disabled, children, title, ...props },
  ref,
) {
  const Comp = asChild ? Slot : 'button'
  return (
    <Comp
      ref={ref}
      data-ui-button={variant || 'primary'}
      data-ui-size={size || 'md'}
      className={cn(buttonVariants({ variant, size, className }))}
      aria-busy={loading || undefined}
      disabled={disabled || loading}
      type={!asChild ? (props.type || 'button') : undefined}
      title={title}
      aria-label={props['aria-label'] || ((size === 'icon' || size === 'icon-sm') ? title : undefined)}
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
