import { useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import * as Dialog from '@radix-ui/react-dialog'
import { Menu, X } from 'lucide-react'
import { PanelMain } from '@/components/layout/AppShell'
import { useNavGroups } from '@/components/layout/Sidebar'
import { Brand, DomainSwitcher, PageNavigation, SearchBox, UserMenu, useIsCustomer } from '@/components/layout/Topbar'
import { useAuth } from '@/store/auth'
import { useUI } from '@/store/ui'
import { ToolIcon } from '../shared/ToolsHome'

// No side menu: every tool lives in the panel behind the Menu button, drawn
// like the home page, so it reads the same from any page.
export function NavDrawer() {
  const open = useUI((state) => state.mobileNavOpen)
  const setOpen = useUI((state) => state.setMobileNavOpen)
  const groups = useNavGroups()
  const location = useLocation()
  useEffect(() => setOpen(false), [location.pathname, location.search, setOpen])
  return <Dialog.Root open={open} onOpenChange={setOpen}><Dialog.Portal>
    <Dialog.Overlay className="inner-drawer-overlay" />
    <Dialog.Content id="panel-navigation-drawer" className="grid-menu-drawer" aria-describedby={undefined} onCloseAutoFocus={(event) => { event.preventDefault(); if (!useUI.getState().paletteOpen) document.querySelector('.inner-menu-button')?.focus() }}>
      <Dialog.Title className="sr-only">Panel navigation</Dialog.Title>
      <div className="grid-menu-head"><Brand tone="white" /><Dialog.Close className="grid-menu-close" aria-label="Close navigation"><X aria-hidden="true" /></Dialog.Close></div>
      <nav className="grid-menu-body" aria-label="Primary navigation">
        {groups.map((group) => <section className="tool-group" key={group.title} aria-label={group.title}>
          <h2 className="grid-menu-title">{group.title}</h2>
          <div className="tool-grid">{group.items.map((item) => item.external
            ? <a key={`${item.to}:${item.label}`} className="tool-link" href={item.to} target="_blank" rel="noopener noreferrer"><ToolIcon icon={item.icon} to={item.to} /><span className="tool-label">{item.label}</span></a>
            : <Link key={`${item.to}:${item.label}`} className="tool-link" to={item.to}><ToolIcon icon={item.icon} to={item.to} /><span className="tool-label">{item.label}</span></Link>)}</div>
        </section>)}
      </nav>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>
}

function MenuToggle() {
  const open = useUI((state) => state.mobileNavOpen)
  const setOpen = useUI((state) => state.setMobileNavOpen)
  return <button type="button" className="inner-menu-button grid-header-pill" aria-label="Menu" aria-expanded={open} aria-controls="panel-navigation-drawer" onClick={() => setOpen(true)}><Menu aria-hidden="true" /><span>Menu</span></button>
}

const levels = { admin: 'Administrator', reseller: 'Reseller' }

export function Layout({ inner, children }) {
  const customer = useIsCustomer()
  const role = useAuth((state) => state.role)
  return <>
    <header className="panel-topbar">
      <div className="topbar-identity"><Brand tone="white" /></div>
      <div className="topbar-actions">
        <MenuToggle />
        {customer && <DomainSwitcher />}
        <span className="grid-level"><span>Access level</span><strong>{levels[role] || 'User'}</strong></span>
        <UserMenu />
      </div>
    </header>
    <div className="grid-search"><SearchBox label="Enter your search criteria…" /></div>
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {inner && <PageNavigation />}
      <PanelMain inner={inner}>{children}</PanelMain>
    </div>
  </>
}
