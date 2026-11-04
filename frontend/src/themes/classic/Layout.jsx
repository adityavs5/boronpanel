import { PanelMain } from '@/components/layout/AppShell'
import { Sidebar } from '@/components/layout/Sidebar'
import { Brand, DomainSwitcher, MenuButton, PageNavigation, SearchBox, UserMenu, useIsCustomer } from '@/components/layout/Topbar'

// Dark side menu holding the logo, with a plain white bar above the page.
export function Layout({ inner, children }) {
  const customer = useIsCustomer()
  return <div className="flex min-h-0 flex-1 overflow-hidden">
    <div className="persistent-navigation"><Sidebar brand={<Brand tone="white" />} /></div>
    <div className="flex min-w-0 flex-1 flex-col">
      <header className="panel-topbar">
        <div className="topbar-identity"><MenuButton /><span className="topbar-compact-brand"><Brand /></span></div>
        <div className="topbar-actions">
          <SearchBox label="Search tools ( / )" />
          {customer && <DomainSwitcher />}
          <UserMenu />
        </div>
      </header>
      {inner && <PageNavigation />}
      <PanelMain inner={inner}>{children}</PanelMain>
    </div>
  </div>
}
