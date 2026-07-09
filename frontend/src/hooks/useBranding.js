import { useQuery } from '@tanstack/react-query'
import { get } from '@/lib/api'

// Run A feature 3: white-label branding. Public endpoint (no auth) -- the
// login page needs this before any session exists. React Query dedupes
// this across every component that calls the hook (Sidebar, Login, the
// title/favicon bootstrap, the admin settings page itself), so it's a
// single network call per page load, not one per consumer.
export function useBranding() {
  const { data } = useQuery({
    queryKey: ['branding'],
    queryFn: () => get('/api/v1/branding'),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  return {
    panelName: data?.panel_name || 'Forgehost',
    logoUrl: data?.logo_url || null,
    faviconUrl: data?.favicon_url || null,
    supportEmail: data?.support_email || null,
    supportUrl: data?.support_url || null,
  }
}
