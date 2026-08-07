import * as TabsPrimitive from '@radix-ui/react-tabs'
import { cn } from '@/lib/cn'

export const Tabs = TabsPrimitive.Root

export function TabsList({ className, ...props }) {
  return (
    <TabsPrimitive.List
      className={cn('flex items-center gap-1 border-b border-border w-full overflow-x-auto scroll-smooth overscroll-x-contain [scrollbar-width:thin]', className)}
      {...props}
    />
  )
}

export function TabsTrigger({ className, ...props }) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        'inline-flex items-center gap-2 whitespace-nowrap border-b-2 border-transparent px-3 py-2.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none data-[state=active]:border-accent data-[state=active]:text-foreground -mb-px',
        className,
      )}
      {...props}
    />
  )
}

export function TabsContent({ className, ...props }) {
  return <TabsPrimitive.Content className={cn('mt-5 focus-visible:outline-none', className)} {...props} />
}
