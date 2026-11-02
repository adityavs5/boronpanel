import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Globe, Mail, FolderOpen, Menu, Users, Activity, Cog, ShieldCheck } from 'lucide-react'
import { useAuth } from '@/store/auth'
import { useUI } from '@/store/ui'

// Compact bottom navigation shown below 768px in place of the sidebar. The
// last slot opens the full navigation drawer.
const customerItems = [
  { label: 'Home', to: '/dashboard', icon: LayoutDashboard },
  { label: 'Domains', to: '/domains', icon: Globe },
  { label: 'Email', to: '/email', icon: Mail },
  { label: 'Files', to: '/files', icon: FolderOpen },
]
const adminItems = [
  { label: 'Home', to: '/overview', icon: LayoutDashboard },
  { label: 'Accounts', to: '/accounts', icon: Users },
  { label: 'Health', to: '/health', icon: Activity },
  { label: 'Services', to: '/services', icon: Cog },
]
const resellerItems = [
  { label: 'Accounts', to: '/reseller', icon: Users },
  { label: 'Security', to: '/security', icon: ShieldCheck },
  { label: 'Settings', to: '/appearance', icon: Cog },
]
const itemClass = 'flex min-h-14 min-w-0 flex-1 flex-col items-center justify-center gap-1 px-1 py-1.5 text-xs font-medium transition-colors'

export function MobileBottomNav() {
  const role = useAuth((s) => s.role)
  const mobileNavOpen = useUI((s) => s.mobileNavOpen)
  const setMobileNavOpen = useUI((s) => s.setMobileNavOpen)
  const items = role === 'admin' ? adminItems : role === 'reseller' ? resellerItems : customerItems
  return (
    <nav className="mobile-bottom-nav fixed inset-x-0 bottom-0 z-40 flex bg-surface md:hidden" style={{ paddingBottom: 'env(safe-area-inset-bottom)' }} aria-label="Mobile navigation">
      {items.map((item) => (
        <NavLink key={`${item.to}-${item.label}`} to={item.to} className={itemClass}>
          <item.icon className="h-5 w-5" aria-hidden="true" />
          <span className="max-w-full truncate">{item.label}</span>
        </NavLink>
      ))}
      <button type="button" className={itemClass} aria-label="All tools" aria-expanded={mobileNavOpen} aria-controls="panel-navigation-drawer" onClick={() => setMobileNavOpen(true)}>
        <Menu className="h-5 w-5" aria-hidden="true" />
        <span>Menu</span>
      </button>
    </nav>
  )
}
