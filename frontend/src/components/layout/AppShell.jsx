import { Suspense, useEffect, useLayoutEffect } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { Skeleton } from '@/components/ui/Skeleton'
import { Topbar, PageNavigation } from './Topbar'
import { MobileBottomNav } from './MobileBottomNav'
import { ImpersonationBanner } from './ImpersonationBanner'
import { useAuth } from '@/store/auth'
import { Sidebar } from './Sidebar'
import { MobileNavDrawer } from './MobileNavDrawer'
import { CommandPalette } from './CommandPalette'

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

// Inner tools have role-aware navigation; dashboard bodies keep their existing layout.
// Search is available throughout the authenticated app.
export function AppShell() {
  const syncIdentity = useAuth((s) => s.syncIdentity)
  const { pathname } = useLocation()
  const inner = !['/overview', '/dashboard', '/reseller'].includes(pathname)
  useLayoutEffect(() => {
    document.documentElement.dataset.innerPage = String(inner)
    return () => { delete document.documentElement.dataset.innerPage }
  }, [inner])

  // Reconcile identity with the server session on mount so a hard refresh
  // restores the correct role and the impersonation banner (Phase 8 f1).
  useEffect(() => { syncIdentity() }, [syncIdentity])

  return (
    <div className={`flex h-full flex-col overflow-hidden ${inner ? 'inner-shell' : 'dashboard-shell'}`}>
      <ImpersonationBanner />
      <Topbar inner={inner} />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {inner && <div className="persistent-navigation"><Sidebar /></div>}
        <div className="flex min-w-0 flex-1 flex-col">
          <PageNavigation />
          <main id="panel-main" className="min-w-0 flex-1 overflow-y-auto bg-background" tabIndex={-1}>
            <div className="panel-content">
              <Suspense fallback={<PageFallback />}>
                <Outlet />
              </Suspense>
            </div>
          </main>
          {inner && <div id="panel-save-region" />}
        </div>
        <MobileBottomNav />
      </div>
      {inner && <MobileNavDrawer />}
      <CommandPalette />
    </div>
  )
}
