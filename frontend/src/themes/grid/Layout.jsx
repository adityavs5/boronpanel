import { Link, useLocation } from 'react-router-dom'
import { ChevronDown, Home } from 'lucide-react'
import { PanelMain } from '@/components/layout/AppShell'
import { useActiveNavItem, useNavGroups } from '@/components/layout/Sidebar'
import { Brand, DomainSwitcher, MenuButton, PageNavigation, SearchBox, UserMenu, useHomePath, useIsCustomer } from '@/components/layout/Topbar'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/DropdownMenu'
import { useAuth } from '@/store/auth'
import { ToolIcon } from '../shared/ToolsHome'

// Every tool stays one menu away from any page, so there is no side menu.
function TopMenu() {
  const groups = useNavGroups()
  const current = useActiveNavItem(groups)
  const home = useHomePath()
  const { pathname } = useLocation()
  return <nav className="grid-menu" aria-label="Primary navigation">
    <Link to={home} className="grid-menu-item" aria-current={pathname === home ? 'page' : undefined}><Home aria-hidden="true" />Home</Link>
    {groups.map((group) => {
      const open = group.items.some((item) => item === current)
      return <DropdownMenu key={group.title}>
        <DropdownMenuTrigger asChild>
          <button type="button" className="grid-menu-item" data-current={open || undefined}>{group.title}<ChevronDown aria-hidden="true" /></button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" sideOffset={0} className="grid-menu-panel">
          {group.items.map((item) => <DropdownMenuItem key={`${item.to}:${item.label}`} asChild>
            {item.external
              ? <a href={item.to} target="_blank" rel="noopener noreferrer"><ToolIcon icon={item.icon} to={item.to} /><span>{item.label}</span></a>
              : <Link to={item.to}><ToolIcon icon={item.icon} to={item.to} /><span>{item.label}</span></Link>}
          </DropdownMenuItem>)}
        </DropdownMenuContent>
      </DropdownMenu>
    })}
  </nav>
}

const levels = { admin: 'Administrator', reseller: 'Reseller' }

export function Layout({ inner, children }) {
  const customer = useIsCustomer()
  const role = useAuth((state) => state.role)
  return <>
    <header className="panel-topbar">
      <div className="topbar-identity"><MenuButton /><Brand tone="white" /></div>
      <div className="topbar-actions">
        {customer && <DomainSwitcher />}
        <span className="grid-level"><span>Access level</span><strong>{levels[role] || 'User'}</strong></span>
        <UserMenu />
      </div>
    </header>
    <TopMenu />
    <div className="grid-search"><SearchBox label="Enter your search criteria…" /></div>
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {inner && <PageNavigation />}
      <PanelMain inner={inner}>{children}</PanelMain>
    </div>
  </>
}
