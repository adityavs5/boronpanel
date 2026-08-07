import { Suspense, useEffect } from 'react'
import { Outlet } from 'react-router-dom'
import { Skeleton } from '@/components/ui/Skeleton'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { MobileBottomNav } from './MobileBottomNav'
import { MobileNavDrawer } from './MobileNavDrawer'
import { ImpersonationBanner } from './ImpersonationBanner'
import { CommandPalette } from './CommandPalette'
import { useAuth } from '@/store/auth'
import { useUI } from '@/store/ui'

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
  const togglePalette = useUI((s) => s.togglePalette)

  // Reconcile identity with the server session on mount so a hard refresh
  // restores the correct role and the impersonation banner (Phase 8 f1).
  useEffect(() => { syncIdentity() }, [syncIdentity])

  useEffect(() => {
    function onKeyDown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        togglePalette()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [togglePalette])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <ImpersonationBanner />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div className="hidden md:flex">
          <Sidebar />
        </div>
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar />
          <main className="flex-1 overflow-y-auto bg-background">
            <div className="mx-auto w-full max-w-[1400px] p-4 pb-24 sm:p-6 md:pb-6">
              <Suspense fallback={<PageFallback />}>
                <Outlet />
              </Suspense>
            </div>
          </main>
        </div>
        <MobileBottomNav />
        <MobileNavDrawer />
      </div>
      <CommandPalette />
    </div>
  )
}
