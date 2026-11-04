import { useEffect } from 'react'
import { useBranding } from '@/hooks/useBranding'
import { currentThemeId, normalizeTheme } from '@/themes/registry'

// Run A feature 3: applies branding to the browser tab. Runs at the root
// (mounted once in main.jsx, alongside <Toaster/>) so it's active on every
// route, including /login before any session exists. Renders nothing.
export function BrandingBootstrap() {
  const { brandingReady, panelName, faviconUrl, theme } = useBranding()

  useEffect(() => {
    if (brandingReady) { const page = document.documentElement.dataset.pageTitle; document.title = page ? `${page} · ${panelName}` : panelName }
  }, [brandingReady, panelName])

  useEffect(() => {
    if (!faviconUrl) return
    let link = document.querySelector('link[rel="icon"]')
    if (!link) {
      link = document.createElement('link')
      link.rel = 'icon'
      document.head.appendChild(link)
    }
    link.href = faviconUrl
  }, [faviconUrl])

  // The administrator changed the theme while this page was open: load the new
  // one. The marker stops a reload loop if a stale copy of the page is served.
  useEffect(() => {
    if (!theme || normalizeTheme(theme) === currentThemeId()) return
    try {
      if (sessionStorage.getItem('boron.theme-reload') === theme) return
      sessionStorage.setItem('boron.theme-reload', theme)
    } catch { return }
    window.location.reload()
  }, [theme])

  return null
}
