import { useState } from 'react'
import { UserCog, LogOut } from 'lucide-react'
import { useAuth } from '@/store/auth'

// Phase 8 feature 1: a persistent, always-visible bar shown whenever an admin
// is impersonating a customer. The "Return to admin" control is always here, so
// the admin can never get "stuck" inside the customer view.
export function ImpersonationBanner() {
  const impersonating = useAuth((s) => s.impersonating)
  const impersonator = useAuth((s) => s.impersonator)
  const username = useAuth((s) => s.username)
  const returnToAdmin = useAuth((s) => s.returnToAdmin)
  const [leaving, setLeaving] = useState(false)

  if (!impersonating) return null

  return (
    <div className="flex items-center justify-between gap-3 bg-amber-500 px-4 py-2 text-sm font-medium text-amber-950">
      <span className="flex items-center gap-2">
        <UserCog className="h-4 w-4 shrink-0" />
        You are viewing this panel as <strong>{username}</strong>
        {impersonator ? <span className="opacity-80">(impersonated by {impersonator})</span> : null}
      </span>
      <button
        type="button"
        disabled={leaving}
        onClick={() => { setLeaving(true); returnToAdmin() }}
        className="inline-flex items-center gap-1.5 rounded-md bg-amber-950/90 px-3 py-1 text-amber-50 hover:bg-amber-950 disabled:opacity-60"
      >
        <LogOut className="h-3.5 w-3.5" /> {leaving ? 'Returning…' : 'Return to admin'}
      </button>
    </div>
  )
}
