import { useParams } from 'react-router-dom'
import { useAuth } from '@/store/auth'

// The account a page operates on: the :username route param when present
// (admin managing someone), otherwise the signed-in customer's own username.
// This lets one resource component serve both the customer page and the admin
// account-detail tab.
export function useAccountUsername() {
  const { username } = useParams()
  const authUsername = useAuth((s) => s.username)
  return username || authUsername
}
