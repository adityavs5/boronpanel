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
    <div role="status" className="impersonation-banner" data-tone={locked ? 'danger' : 'warning'}>
      <span className="impersonation-context">
        <BadgeAlert aria-hidden="true" />
        <span>{data.message}{locked ? ' Administration is limited until the license is fixed.' : ''}</span>
      </span>
      {!pathname.endsWith('/license') && (
        <Link to="/license" className="impersonation-return">
          Open license
        </Link>
      )}
    </div>
  )
}
