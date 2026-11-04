import { Suspense, useEffect, useLayoutEffect } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { Skeleton } from '@/components/ui/Skeleton'
import { MobileBottomNav } from './MobileBottomNav'
import { ImpersonationBanner } from './ImpersonationBanner'
import { LicenseBanner } from './LicenseBanner'
import { useAuth } from '@/store/auth'
import { MobileNavDrawer } from './MobileNavDrawer'
import { CommandPalette } from './CommandPalette'
import { useBranding } from '@/hooks/useBranding'
import { routeTitle } from '@/lib/route-title'
import { activeTheme } from '@/themes/runtime'

// Shown while a code-split page chunk loads (usually <100ms on repeat visits).
function PageFallback() {
  return (
    <div aria-busy="true">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="mt-3 h-7 w-64" />
      <Skeleton className="mt-2 h-4 w-96 max-w-full" />
      <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="mt-6 h-72" />
    </div>
  )
}

// The scrolling page area every theme's layout puts its content in.
export function PanelMain({ inner, children }) {
  return <>
    <main id="panel-main" className="min-w-0 flex-1 overflow-y-auto bg-background" tabIndex={-1}>
      <div className="panel-content">{children}</div>
    </main>
    {inner && <div id="panel-save-region" />}
  </>
}

// Session handling and page titles live here; the server's theme draws the
// frame around the page. Search is available throughout the authenticated app.
export function AppShell() {
  const syncIdentity = useAuth((s) => s.syncIdentity)
  const identitySynced = useAuth((s) => s.identitySynced)
  const role = useAuth((s) => s.role)
  const { panelName } = useBranding()
  const { pathname, search } = useLocation()
  const { Layout, NavDrawer = MobileNavDrawer } = activeTheme()
  const inner = !['/overview', '/dashboard'].includes(pathname)
  useLayoutEffect(() => {
    document.documentElement.dataset.innerPage = String(inner)
    return () => { delete document.documentElement.dataset.innerPage }
  }, [inner])
  useLayoutEffect(() => {
    const title = routeTitle(pathname, search, role)
    document.documentElement.dataset.pageTitle = title
    document.title = `${title} · ${panelName || 'Boron Panel'}`
  }, [pathname, search, role, panelName])

  // Reconcile identity with the server session on mount so a hard refresh
  // restores the correct role and the impersonation banner (Phase 8 f1).
  useEffect(() => { syncIdentity() }, [syncIdentity])

  return (
    <div className={`flex h-full flex-col overflow-hidden ${inner ? 'inner-shell' : 'dashboard-shell'}`}>
      <ImpersonationBanner />
      <LicenseBanner />
      <Layout inner={inner}>
        <Suspense fallback={<PageFallback />}>
          {identitySynced ? <Outlet /> : <PageFallback />}
        </Suspense>
      </Layout>
      <MobileBottomNav />
      <NavDrawer />
      <CommandPalette />
    </div>
  )
}
