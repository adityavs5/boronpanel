import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/cn'

export function Spinner({ className, size = 20 }) {
  return <Loader2 className={cn('animate-spin text-muted-foreground', className)} style={{ width: size, height: size }} />
}

export function CenteredSpinner({ label }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-muted-foreground">
      <Spinner size={28} />
      {label && <p className="text-sm">{label}</p>}
    </div>
  )
}
