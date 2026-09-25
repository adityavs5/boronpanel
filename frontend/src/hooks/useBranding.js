import { useQuery } from '@tanstack/react-query'
import { get } from '@/lib/api'

const BRANDING_CACHE_KEY = 'boron.public-branding.v1'

function validAssetUrl(value) {
  return value == null || value === '/api/v1/branding/logo' || value === '/api/v1/branding/favicon'
}

function normalizeBranding(value) {
  if (!value || typeof value !== 'object') return null
  if (typeof value.panel_name !== 'string' || !value.panel_name.trim() || value.panel_name.length > 120) return null
  if (!validAssetUrl(value.logo_url) || !validAssetUrl(value.favicon_url)) return null
  return {
    panel_name: value.panel_name,
    logo_url: value.logo_url || null,
    favicon_url: value.favicon_url || null,
    support_email: typeof value.support_email === 'string' ? value.support_email : null,
    support_url: typeof value.support_url === 'string' ? value.support_url : null,
  }
}

function cachedBranding() {
  try { return normalizeBranding(JSON.parse(localStorage.getItem(BRANDING_CACHE_KEY))) || undefined }
  catch { return undefined }
}

async function fetchBranding() {
  const data = normalizeBranding(await get('/api/v1/branding'))
  if (data) {
    try { localStorage.setItem(BRANDING_CACHE_KEY, JSON.stringify(data)) } catch { /* storage can be unavailable */ }
  }
  return data
}

// Run A feature 3: white-label branding. Public endpoint (no auth) -- the
// login page needs this before any session exists. React Query dedupes
// this across every component that calls the hook (Sidebar, Login, the
// title/favicon bootstrap, the admin settings page itself), so it's a
// single network call per page load, not one per consumer.
export function useBranding() {
  const { data, isError } = useQuery({
    queryKey: ['branding'],
    queryFn: fetchBranding,
    initialData: cachedBranding,
    initialDataUpdatedAt: 0,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const brandingReady = data !== undefined || isError
  return {
    brandingReady,
    panelName: brandingReady ? (data?.panel_name || 'Boron') : '',
    logoUrl: brandingReady ? (data?.logo_url || null) : undefined,
    faviconUrl: brandingReady ? (data?.favicon_url || null) : undefined,
    supportEmail: data?.support_email || null,
    supportUrl: data?.support_url || null,
  }
}
