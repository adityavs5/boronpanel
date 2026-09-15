import { Suspense, useEffect } from 'react'
import { Outlet } from 'react-router-dom'
import { Skeleton } from '@/components/ui/Skeleton'
import { Topbar, PageNavigation } from './Topbar'
import { MobileBottomNav } from './MobileBottomNav'
import { ImpersonationBanner } from './ImpersonationBanner'
import { useAuth } from '@/store/auth'

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

// The authenticated app frame: iron sidebar (md+), topbar, scrollable content,
// a mobile bottom nav below 768px, and the Ctrl/Cmd+K command palette.
export function AppShell() {
  const syncIdentity = useAuth((s) => s.syncIdentity)

  // Reconcile identity with the server session on mount so a hard refresh
  // restores the correct role and the impersonation banner (Phase 8 f1).
  useEffect(() => { syncIdentity() }, [syncIdentity])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <ImpersonationBanner />
      <Topbar />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div className="flex min-w-0 flex-1 flex-col">
          <PageNavigation />
          <main className="flex-1 overflow-y-auto bg-background">
            <div className="panel-content">
              <Suspense fallback={<PageFallback />}>
                <Outlet />
              </Suspense>
            </div>
          </main>
        </div>
        <MobileBottomNav />
      </div>
    </div>
  )
}
