import { NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { PanelLeftClose, PanelLeftOpen, Server, Cpu, MemoryStick, HardDrive } from 'lucide-react'
import { cn } from '@/lib/cn'
import { get } from '@/lib/api'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { customerNav, adminNav } from '@/config/nav'
import { Tooltip } from '@/components/ui/Tooltip'
import { ProgressBar } from '@/components/ui/Progress'
import { APP_VERSION } from '@/config/constants'

function NavItem({ item, collapsed }) {
  const Icon = item.icon
  const link = (
    <NavLink
      to={item.to}
      className={({ isActive }) =>
        cn(
          'group flex items-center gap-3 rounded-btn px-3 py-2 text-sm font-medium transition-colors',
          collapsed && 'justify-center px-0',
          isActive
            ? 'bg-accent/15 text-accent-300'
            : 'text-sidebar-muted hover:bg-sidebar-hover hover:text-white',
        )
      }
    >
      <Icon className="h-[18px] w-[18px] shrink-0" />
      {!collapsed && <span className="truncate">{item.label}</span>}
    </NavLink>
  )
  return collapsed ? (
    <Tooltip content={item.label} side="right">
      {link}
    </Tooltip>
  ) : (
    link
  )
}

function HealthMiniWidget({ collapsed }) {
  const { data } = useQuery({
    queryKey: ['health'],
    queryFn: () => get('/api/v1/health'),
    refetchInterval: 15_000,
    retry: false,
  })
  if (!data) return null
  const disk = data.disks?.find((d) => d.mount === '/') || data.disks?.[0]
  if (collapsed) {
    return (
      <Tooltip content={`CPU ${Math.round(data.cpu_pct)}% · RAM ${Math.round(data.mem_pct)}%`} side="right">
        <div className="mx-auto flex h-9 w-9 items-center justify-center rounded-btn bg-sidebar-hover text-sidebar-muted">
          <Server className="h-[18px] w-[18px]" />
        </div>
      </Tooltip>
    )
  }
  const rows = [
    { icon: Cpu, label: 'CPU', pct: data.cpu_pct },
    { icon: MemoryStick, label: 'RAM', pct: data.mem_pct },
    { icon: HardDrive, label: 'Disk', pct: disk?.pct },
  ]
  return (
    <div className="rounded-card bg-sidebar-hover/60 p-3 space-y-2">
      <div className="flex items-center gap-2 text-xs font-medium text-white">
        <Server className="h-3.5 w-3.5 text-accent-400" /> Server Health
      </div>
      {rows.map((r) => (
        <div key={r.label} className="space-y-1">
          <div className="flex items-center justify-between text-[11px] text-sidebar-muted">
            <span className="flex items-center gap-1">
              <r.icon className="h-3 w-3" /> {r.label}
            </span>
            <span className="tabular-nums text-gray-300">{r.pct != null ? `${Math.round(r.pct)}%` : '—'}</span>
          </div>
          <ProgressBar value={r.pct ?? 0} size="sm" showTrack className="bg-gray-700" />
        </div>
      ))}
    </div>
  )
}

export function Sidebar() {
  const collapsed = useUI((s) => s.sidebarCollapsed)
  const toggle = useUI((s) => s.toggleSidebar)
  const isAdmin = useAuth((s) => s.role === 'admin')
  const nav = isAdmin ? adminNav : customerNav

  return (
    <aside
      className={cn(
        'flex h-full flex-col bg-sidebar text-white transition-[width] duration-200 ease-in-out',
        collapsed ? 'w-16' : 'w-60',
      )}
    >
      {/* Logo + version */}
      <div className={cn('flex h-16 items-center gap-2.5 border-b border-sidebar-border px-4', collapsed && 'justify-center px-0')}>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-btn bg-accent text-accent-foreground font-bold">F</div>
        {!collapsed && (
          <div className="min-w-0">
            <div className="text-sm font-semibold leading-tight text-white">Forgehost</div>
            <div className="text-[11px] leading-tight text-sidebar-muted">
              {isAdmin ? 'Admin' : 'Customer'} · {APP_VERSION}
            </div>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2.5 py-3">
        {nav.map((item, i) =>
          item.section ? (
            !collapsed && (
              <div key={`s-${i}`} className="px-3 pb-1 pt-4 text-[10px] font-semibold uppercase tracking-wider text-gray-500 first:pt-1">
                {item.section}
              </div>
            )
          ) : (
            <NavItem key={item.to} item={item} collapsed={collapsed} />
          ),
        )}
      </nav>

      {/* Health widget (admin) + collapse toggle */}
      <div className="space-y-2 border-t border-sidebar-border p-2.5">
        {isAdmin && <HealthMiniWidget collapsed={collapsed} />}
        <button
          onClick={toggle}
          className={cn(
            'flex w-full items-center gap-3 rounded-btn px-3 py-2 text-sm text-sidebar-muted transition-colors hover:bg-sidebar-hover hover:text-white',
            collapsed && 'justify-center px-0',
          )}
        >
          {collapsed ? <PanelLeftOpen className="h-[18px] w-[18px]" /> : <PanelLeftClose className="h-[18px] w-[18px]" />}
          {!collapsed && <span>Collapse</span>}
        </button>
      </div>
    </aside>
  )
}
