import { useEffect } from 'react'
import { useBranding } from '@/hooks/useBranding'

// Run A feature 3: applies branding to the browser tab. Runs at the root
// (mounted once in main.jsx, alongside <Toaster/>) so it's active on every
// route, including /login before any session exists. Renders nothing.
export function BrandingBootstrap() {
  const { brandingReady, panelName, faviconUrl } = useBranding()

  useEffect(() => {
    if (brandingReady) document.title = panelName
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

  return null
}
