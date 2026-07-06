import * as DM from '@radix-ui/react-dropdown-menu'
import { cn } from '@/lib/cn'

export const DropdownMenu = DM.Root
export const DropdownMenuTrigger = DM.Trigger
export const DropdownMenuGroup = DM.Group

export function DropdownMenuContent({ className, align = 'end', sideOffset = 6, ...props }) {
  return (
    <DM.Portal>
      <DM.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          'z-50 min-w-[10rem] overflow-hidden rounded-card border border-border bg-card p-1 shadow-dropdown data-[state=open]:animate-scale-in',
          className,
        )}
        {...props}
      />
    </DM.Portal>
  )
}

export function DropdownMenuItem({ className, inset, destructive, ...props }) {
  return (
    <DM.Item
      className={cn(
        'relative flex cursor-pointer select-none items-center gap-2 rounded-btn px-2.5 py-1.5 text-sm text-foreground outline-none transition-colors focus:bg-muted data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        destructive && 'text-danger focus:bg-danger/10',
        inset && 'pl-8',
        className,
      )}
      {...props}
    />
  )
}

export function DropdownMenuLabel({ className, ...props }) {
  return <DM.Label className={cn('px-2.5 py-1.5 text-xs font-semibold text-muted-foreground', className)} {...props} />
}

export function DropdownMenuSeparator({ className, ...props }) {
  return <DM.Separator className={cn('-mx-1 my-1 h-px bg-border', className)} {...props} />
}
