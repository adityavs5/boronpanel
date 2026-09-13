import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Globe, Mail, Database, Menu, Users, Activity, Cog } from 'lucide-react'
import { cn } from '@/lib/cn'
import { useAuth } from '@/store/auth'

// Compact bottom navigation shown below 768px in place of the sidebar.
const customerItems = [
  { label: 'Home', to: '/dashboard', icon: LayoutDashboard },
  { label: 'Domains', to: '/domains', icon: Globe },
  { label: 'Email', to: '/email', icon: Mail },
  { label: 'DBs', to: '/databases', icon: Database },
  { label: 'WordPress', to: '/wordpress', icon: Globe },
]
const adminItems = [
  { label: 'Home', to: '/overview', icon: LayoutDashboard },
  { label: 'Accounts', to: '/accounts', icon: Users },
  { label: 'Health', to: '/health', icon: Activity },
  { label: 'Services', to: '/services', icon: Cog },
  { label: 'WordPress', to: '/wordpress', icon: Globe },
]

export function MobileBottomNav() {
  const isAdmin = useAuth((s) => s.role === 'admin')
  const items = isAdmin ? adminItems : customerItems
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 flex border-t border-border bg-surface md:hidden" style={{ paddingBottom: 'env(safe-area-inset-bottom)' }} aria-label="Mobile navigation">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            cn(
              'flex min-h-12 flex-1 flex-col items-center justify-center gap-0.5 py-1.5 text-[11px] font-medium transition-colors',
              isActive ? 'text-accent-600' : 'text-muted-foreground',
            )
          }
        >
          <item.icon className="h-5 w-5" />
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}
