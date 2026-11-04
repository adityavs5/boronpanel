import { Link, useLocation } from 'react-router-dom'
import { PanelMain } from '@/components/layout/AppShell'
import { Sidebar } from '@/components/layout/Sidebar'
import { Brand, DomainSwitcher, MenuButton, PageNavigation, SearchBox, UserMenu, useHomePath, useIsCustomer } from '@/components/layout/Topbar'
import { useBranding } from '@/hooks/useBranding'
import { useVersion } from '@/hooks/useVersion'
import { useAuth } from '@/store/auth'

function Footer() {
  const home = useHomePath()
  const version = useVersion()
  const { supportUrl, supportEmail } = useBranding()
  const support = supportUrl || (supportEmail && `mailto:${supportEmail}`)
  return <footer className="classic-footer">
    <span className="classic-footer-brand"><Brand /><span>{version}</span></span>
    <nav aria-label="Footer">
      <Link to={home}>Home</Link>
      <Link to="/appearance">Display preferences</Link>
      {support && <a href={support} target="_blank" rel="noopener noreferrer">Contact support</a>}
    </nav>
  </footer>
}

// Dark side menu holding the logo, with a plain white bar above the page.
export function Layout({ inner, children }) {
  const customer = useIsCustomer()
  const admin = useAuth((state) => state.role) === 'admin'
  const { pathname } = useLocation()
  // Top-level pages need no trail: the menu shows where you are. Nested pages
  // keep the way back, and administrators keep the account switcher it holds.
  const trail = inner && (admin || pathname.split('/').filter(Boolean).length > 1)
  return <div className="flex min-h-0 flex-1 overflow-hidden">
    <div className="persistent-navigation"><Sidebar brand={<Brand tone="white" />} /></div>
    <div className="flex min-w-0 flex-1 flex-col">
      <header className="panel-topbar">
        <div className="topbar-identity"><MenuButton /><span className="topbar-compact-brand"><Brand /></span></div>
        <div className="topbar-actions">
          <SearchBox label="Search Tools ( / )" />
          {customer && <DomainSwitcher />}
          <UserMenu />
        </div>
      </header>
      {trail && <PageNavigation />}
      <PanelMain inner={inner}>{children}<Footer /></PanelMain>
    </div>
  </div>
}
