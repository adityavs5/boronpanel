import { forwardRef } from 'react'
import { cn } from '@/lib/cn'

// Native select styled to match the design system — simpler and more robust
// than a Radix listbox for the many plain dropdowns across the panel.
export const Select = forwardRef(function Select({ className, invalid, children, ...props }, ref) {
  return (
    <select
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        'h-9 w-full rounded-btn border border-input bg-input-surface pl-3 text-sm text-foreground shadow-sm transition-[border-color,box-shadow] focus-visible:outline-none focus-visible:border-accent focus-visible:ring-[3px] focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50',
        invalid && 'border-danger focus-visible:border-danger focus-visible:ring-danger/20',
        className,
      )}
      {...props}
    >
      {children}
    </select>
  )
})
