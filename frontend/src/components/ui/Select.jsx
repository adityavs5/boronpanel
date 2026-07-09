import { forwardRef } from 'react'
import { cn } from '@/lib/cn'

// Native select styled to match the design system — simpler and more robust
// than a Radix listbox for the many plain dropdowns across the panel.
export const Select = forwardRef(function Select({ className, invalid, children, ...props }, ref) {
  return (
    <select
      ref={ref}
      className={cn(
        'h-9 w-full rounded-btn border border-input bg-surface px-3 pr-8 text-sm text-foreground shadow-sm transition-[border-color,box-shadow] focus-visible:outline-none focus-visible:border-accent focus-visible:ring-[3px] focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50 appearance-none bg-no-repeat',
        invalid && 'border-danger focus-visible:border-danger focus-visible:ring-danger/20',
        className,
      )}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%239CA3AF' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>\")",
        backgroundPosition: 'right 0.5rem center',
      }}
      {...props}
    >
      {children}
    </select>
  )
})
