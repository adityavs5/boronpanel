import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/store/auth'

// Gate for authenticated routes. Identity comes from the persisted auth store;
// if the underlying session cookie has expired, the first API call 401s and the
// axios interceptor redirects to /login anyway.
export function ProtectedRoute({ children, adminOnly = false, customerOnly = false, resellerOnly = false }) {
  const role = useAuth((s) => s.role)
  const location = useLocation()

  if (!role) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  if (adminOnly && role !== 'admin') {
    return <Navigate to={role === 'reseller' ? '/reseller' : '/dashboard'} replace />
  }
  if (customerOnly && role !== 'customer') {
    return <Navigate to={role === 'reseller' ? '/reseller' : '/accounts'} replace />
  }
  if (resellerOnly && role !== 'reseller') {
    return <Navigate to={role === 'admin' ? '/overview' : '/dashboard'} replace />
  }
  return children
}
