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
    <div className="impersonation-banner bg-amber-500 text-sm font-medium text-amber-950">
      <span className="impersonation-context">
        <UserCog className="h-4 w-4 shrink-0" />
        <span>You are viewing this panel as <strong>{username}</strong>
          {impersonator ? <span className="impersonation-actor"> (impersonated by {impersonator})</span> : null}
        </span>
      </span>
      <button
        type="button"
        disabled={leaving}
        onClick={() => { setLeaving(true); returnToAdmin() }}
        className="impersonation-return bg-amber-950/90 text-amber-50 hover:bg-amber-950 disabled:opacity-60"
      >
        <LogOut className="h-3.5 w-3.5" /> {leaving ? 'Returning…' : 'Return to admin'}
      </button>
    </div>
  )
}
