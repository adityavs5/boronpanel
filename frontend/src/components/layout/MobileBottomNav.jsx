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
  { label: 'More', to: '/more', icon: Menu },
]
const adminItems = [
  { label: 'Accounts', to: '/accounts', icon: Users },
  { label: 'Health', to: '/health', icon: Activity },
  { label: 'Services', to: '/services', icon: Cog },
  { label: 'More', to: '/more', icon: Menu },
]

export function MobileBottomNav() {
  const isAdmin = useAuth((s) => s.role === 'admin')
  const items = isAdmin ? adminItems : customerItems
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 flex border-t border-border bg-surface md:hidden">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            cn(
              'flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] font-medium transition-colors',
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
