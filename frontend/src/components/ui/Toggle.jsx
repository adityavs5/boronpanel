import { forwardRef } from 'react'
import * as SwitchPrimitive from '@radix-ui/react-switch'
import * as CheckboxPrimitive from '@radix-ui/react-checkbox'
import { Check } from 'lucide-react'
import { cn } from '@/lib/cn'

export const Switch = forwardRef(function Switch({ className, ...props }, ref) {
  return (
    <SwitchPrimitive.Root
      ref={ref}
      className={cn(
        'peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-accent data-[state=unchecked]:bg-border',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block h-4 w-4 rounded-full bg-white shadow ring-0 transition-transform data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0" />
    </SwitchPrimitive.Root>
  )
})

export const Checkbox = forwardRef(function Checkbox({ className, ...props }, ref) {
  return (
    <CheckboxPrimitive.Root
      ref={ref}
      className={cn(
        'peer h-4 w-4 shrink-0 rounded-sm border border-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-accent data-[state=checked]:border-accent data-[state=checked]:text-accent-foreground',
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="flex items-center justify-center text-current">
        <Check className="h-3 w-3" strokeWidth={3} />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
})
