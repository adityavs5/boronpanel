import * as DialogPrimitive from '@radix-ui/react-dialog'
import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { Button } from './Button'
import { Input, FormField } from './Input'

export const Dialog = DialogPrimitive.Root
export const DialogTrigger = DialogPrimitive.Trigger
export const DialogClose = DialogPrimitive.Close

export function DialogContent({ className, children, size = 'md', showClose = true, ...props }) {
  const sizes = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl', xl: 'max-w-4xl', full: 'max-w-6xl' }
  return (
    <DialogPrimitive.Portal>
      {/* Run A feature 2: no backdrop-blur (goal: remove all blur/glass) —
          the dim scrim alone is enough to focus the modal. */}
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/50 data-[state=open]:animate-fade-in" />
      <DialogPrimitive.Content
        className={cn(
          'panel-dialog fixed left-1/2 top-1/2 z-50 flex max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-card border border-border bg-card shadow-dropdown focus:outline-none data-[state=open]:animate-scale-in',
          sizes[size],
          className,
        )}
        {...props}
      >
        {children}
        {showClose && (
          <DialogPrimitive.Close className="absolute right-4 top-4 rounded-sm p-1 text-muted-foreground opacity-70 transition-opacity hover:opacity-100 hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring">
            <X className="h-4 w-4" />
            <span className="sr-only">Close</span>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  )
}

export function DialogHeader({ className, ...props }) {
  return <div className={cn('px-6 pt-5 pb-2', className)} {...props} />
}
export function DialogTitle({ className, ...props }) {
  return <DialogPrimitive.Title className={cn('text-base font-semibold text-foreground pr-6', className)} {...props} />
}
export function DialogDescription({ className, ...props }) {
  return <DialogPrimitive.Description className={cn('text-sm text-muted-foreground mt-1', className)} {...props} />
}
export function DialogBody({ className, ...props }) {
  return <div className={cn('min-h-0 flex-1 overflow-y-auto px-6 py-3', className)} {...props} />
}
export function DialogFooter({ className, ...props }) {
  return <div className={cn('flex shrink-0 flex-wrap items-center justify-end gap-3 border-t border-border px-6 py-4', className)} {...props} />
}

// Confirmation dialog for destructive actions (goal: "confirmation modals on
// destructive"). Controlled via `open`/`onOpenChange`.
export function ConfirmDialog({
  open,
  onOpenChange,
  title = 'Are you sure?',
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  variant = 'danger',
  loading = false,
  onConfirm,
  confirmationText,
  acknowledgementLabel,
}) {
  const [typed, setTyped] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  useEffect(() => { if (!open) { setTyped(''); setAcknowledged(false) } }, [open])
  const confirmed = (!confirmationText || typed === confirmationText) && (!acknowledgementLabel || acknowledged)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {acknowledgementLabel && <DialogBody><label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 h-4 w-4 accent-accent" checked={acknowledged} disabled={loading} onChange={event => setAcknowledged(event.target.checked)}/><span>{acknowledgementLabel}</span></label></DialogBody>}
        {confirmationText && (
          <DialogBody>
            <FormField label={<>Type <span className="font-mono font-semibold">{confirmationText}</span> to confirm</>}>
              <Input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} />
            </FormField>
          </DialogBody>
        )}
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button variant={variant} onClick={onConfirm} loading={loading} disabled={!confirmed || loading}>
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
