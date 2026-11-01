import { useQuery } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router-dom'
import { BadgeAlert } from 'lucide-react'
import { get } from '@/lib/api'
import { useAuth } from '@/store/auth'

// Shown to administrators whenever the server license needs attention. The
// state endpoint is local to the panel (no license-server call), so polling
// it is cheap.
export function LicenseBanner() {
  const role = useAuth((s) => s.role)
  const impersonating = useAuth((s) => s.impersonating)
  const { pathname } = useLocation()
  const isAdmin = role === 'admin' && !impersonating
  const { data } = useQuery({
    queryKey: ['license-state'],
    queryFn: () => get('/api/v1/admin/license/state'),
    enabled: isAdmin,
    refetchInterval: 5 * 60 * 1000,
    retry: false,
  })
  if (!isAdmin || !data || data.state === 'active') return null
  const locked = !data.usable
  return (
    <div role="status" className={`impersonation-banner text-sm font-medium ${locked ? 'bg-red-600 text-white' : 'bg-amber-500 text-amber-950'}`}>
      <span className="impersonation-context">
        <BadgeAlert className="h-4 w-4 shrink-0" />
        <span>{data.message}{locked ? ' Administration is limited until the license is fixed.' : ''}</span>
      </span>
      {!pathname.endsWith('/license') && (
        <Link to="/license" className={`impersonation-return ${locked ? 'bg-white/90 text-red-700 hover:bg-white' : 'bg-amber-950/90 text-amber-50 hover:bg-amber-950'}`}>
          Open License
        </Link>
      )}
    </div>
  )
}
