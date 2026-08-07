import { create } from 'zustand'
import * as ToastPrimitive from '@radix-ui/react-toast'
import { CheckCircle2, AlertCircle, Info, X, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/cn'

// Global toast store — call toast.success('...'), toast.error(err), etc. from
// anywhere (including outside React, e.g. mutation onError handlers).
let idSeq = 0
const useToastStore = create((set) => ({
  toasts: [],
  add: (t) => {
    const id = ++idSeq
    set((s) => ({ toasts: [...s.toasts, { id, duration: 4500, ...t }] }))
    return id
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

function push(variant, title, description) {
  return useToastStore.getState().add({ variant, title, description })
}

export const toast = {
  success: (title, description) => push('success', title, description),
  error: (titleOrErr, description) => {
    const title = typeof titleOrErr === 'string' ? titleOrErr : titleOrErr?.message || 'Something went wrong'
    return push('error', title, description)
  },
  warning: (title, description) => push('warning', title, description),
  info: (title, description) => push('info', title, description),
}

const ICONS = {
  success: CheckCircle2,
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
}
const ACCENTS = {
  success: 'text-success',
  error: 'text-danger',
  warning: 'text-warning',
  info: 'text-info',
}

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts)
  const dismiss = useToastStore((s) => s.dismiss)
  return (
    <ToastPrimitive.Provider swipeDirection="right">
      {toasts.map((t) => {
        const Icon = ICONS[t.variant] || Info
        const rail = {
          success: 'border-l-success',
          error: 'border-l-danger',
          warning: 'border-l-warning',
          info: 'border-l-info',
        }[t.variant] || 'border-l-border'
        return (
          <ToastPrimitive.Root
            key={t.id}
            duration={t.duration}
            onOpenChange={(open) => !open && dismiss(t.id)}
            className={cn(
              'pointer-events-auto flex w-full items-start gap-3 rounded-card border border-border border-l-2 bg-card p-4 shadow-dropdown data-[state=open]:animate-slide-in-right data-[swipe=end]:animate-fade-in',
              rail,
            )}
          >
            <Icon className={cn('mt-0.5 h-5 w-5 shrink-0', ACCENTS[t.variant])} />
            <div className="flex-1 min-w-0">
              <ToastPrimitive.Title className="text-sm font-medium text-foreground">{t.title}</ToastPrimitive.Title>
              {t.description && (
                <ToastPrimitive.Description className="mt-0.5 text-xs text-muted-foreground break-words">
                  {t.description}
                </ToastPrimitive.Description>
              )}
            </div>
            <ToastPrimitive.Close className="rounded-sm p-0.5 text-muted-foreground hover:text-foreground">
              <X className="h-4 w-4" />
            </ToastPrimitive.Close>
          </ToastPrimitive.Root>
        )
      })}
      <ToastPrimitive.Viewport className="fixed bottom-[calc(3.5rem+env(safe-area-inset-bottom))] right-0 z-[100] flex w-full max-w-sm flex-col gap-2 p-4 outline-none md:bottom-0" />
    </ToastPrimitive.Provider>
  )
}
