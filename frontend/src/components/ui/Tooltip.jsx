import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { cn } from '@/lib/cn'

export const TooltipProvider = TooltipPrimitive.Provider

export function Tooltip({ content, children, side = 'top', className }) {
  if (!content) return children
  return (
    <TooltipPrimitive.Root delayDuration={200}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          className={cn(
            // Run A feature 2: shadow-dropdown is neutralized in dark mode
            // (index.css) — this tooltip is always dark regardless of theme,
            // so it needs its own border for definition once the shadow is gone.
            'z-50 max-w-xs rounded-btn border border-gray-700 bg-gray-900 px-2.5 py-1.5 text-xs text-white shadow-dropdown data-[state=delayed-open]:animate-fade-in',
            className,
          )}
        >
          {content}
          <TooltipPrimitive.Arrow className="fill-gray-900" />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}
