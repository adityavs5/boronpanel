import { useEffect } from 'react'
import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { MobileBottomNav } from './MobileBottomNav'
import { ImpersonationBanner } from './ImpersonationBanner'
import { useAuth } from '@/store/auth'

// The authenticated app frame: dark sidebar (md+), topbar, scrollable content
// with 24px padding, and a mobile bottom nav below 768px.
export function AppShell() {
  const syncIdentity = useAuth((s) => s.syncIdentity)
  // Reconcile identity with the server session on mount so a hard refresh
  // restores the correct role and the impersonation banner (Phase 8 f1).
  useEffect(() => { syncIdentity() }, [syncIdentity])

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
              <Outlet />
            </div>
          </main>
        </div>
        <MobileBottomNav />
      </div>
    </div>
  )
}
