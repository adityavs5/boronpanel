import { PanelMain } from '@/components/layout/AppShell'
import { Sidebar } from '@/components/layout/Sidebar'
import { Brand, ColorModeToggle, DomainSwitcher, MenuButton, PageNavigation, SearchBox, UserMenu, useIsCustomer } from '@/components/layout/Topbar'

// Light side menu of hubs, with search leading the bar above the page.
export function Layout({ inner, children }) {
  const customer = useIsCustomer()
  return <div className="flex min-h-0 flex-1 overflow-hidden">
    <div className="persistent-navigation"><Sidebar brand={<Brand />} /></div>
    <div className="flex min-w-0 flex-1 flex-col">
      <header className="panel-topbar">
        <div className="topbar-identity"><MenuButton /><span className="topbar-compact-brand"><Brand /></span><SearchBox label="Search tools and settings" /></div>
        <div className="topbar-actions">
          {customer && <DomainSwitcher />}
          <ColorModeToggle />
          <UserMenu />
        </div>
      </header>
      {inner && <PageNavigation />}
      <PanelMain inner={inner}>{children}</PanelMain>
    </div>
  </div>
}
