import { useQuery } from '@tanstack/react-query'
import { get } from '@/lib/api'
import { useAuth } from '@/store/auth'

// Admin-only panel-update status: current/latest version, whether an update
// is available, the active/last job, rollback availability. Drives the
// sidebar badge, the dashboard banner and the Updates page. Polls fast only
// while a job is actually running (the daemon caches the GitHub check for
// 1h, so the idle poll is a cheap local read). refetchInterval keeps firing
// through fetch errors -- exactly what we want while the panel services
// restart mid-update.
const ACTIVE = new Set(['pending', 'running', 'finalizing'])

export function isUpdateJobActive(status) {
  return ACTIVE.has(status?.active_job?.status)
}

export function useUpdateStatus() {
  const isAdmin = useAuth((s) => s.role === 'admin')
  return useQuery({
    queryKey: ['update-status'],
    queryFn: () => get('/api/v1/admin/update/status'),
    enabled: isAdmin,
    retry: false,
    refetchInterval: (query) => (isUpdateJobActive(query.state.data) ? 2500 : 5 * 60_000),
  })
}
