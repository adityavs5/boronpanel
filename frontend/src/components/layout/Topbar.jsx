import { useState } from 'react'
import { useNavigate, useLocation, Link, useParams, useMatches } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Palette, Home, Sun, Moon, LogOut, ChevronDown, User, KeyRound, Check, ChevronsUpDown, ShieldCheck, Globe2, Search, Menu, ArrowLeft, ChevronRight } from 'lucide-react'
import { ThemeSelector } from '@/components/themes/ThemeSelector'
import { useBranding } from '@/hooks/useBranding'
import { get } from '@/lib/api'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { routeTitle } from '@/lib/route-title'
import { adminNav, customerNav, resellerNav } from '@/config/nav'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useAccountUsername } from '@/hooks/useAccount'
import { useDomainContext } from '@/hooks/useDomainContext'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'

const SEGMENT_LABELS = { plans: 'Plans', import: 'Import', software: 'Software', new: 'New', edit: 'Edit', email: 'Email', accounts: 'Accounts', domains: 'Domains' }
const EXACT_LABELS = { '/plans/new': 'New plan', '/import/accounts': 'Account migrations', '/accounts/new': 'Create account' }
const sentence = (text) => { const words = text.replace(/[-_]+/g, ' '); return words.charAt(0).toUpperCase() + words.slice(1) }

// Crumb labels come from the page names (route-title / navigation), not the
// URL slug, so the trail matches the page heading.
function crumbLabel(to, part, role, notFound, last) {
  if (last && notFound) return 'Not found'
  if (EXACT_LABELS[to]) return EXACT_LABELS[to]
  if (/^\/accounts\/[^/]+$/.test(to) || /\/domains\/[^/]+$/.test(to)) return decodeURIComponent(part)
  const title = routeTitle(to, '', role)
  if (title !== 'Boron Panel') return title
  return SEGMENT_LABELS[part] || sentence(decodeURIComponent(part))
}

function useCrumbs() {
  const { pathname } = useLocation()
  const role = useAuth(state => state.role)
  const matches = useMatches()
  const notFound = matches.length > 0 && matches[matches.length - 1].params?.['*'] !== undefined
  const nav = role === 'admin' ? adminNav : role === 'reseller' ? resellerNav : customerNav
  const reachable = new Set(nav.filter(item => !item.external && item.to).map(item => item.to.split('?')[0]))
  const parts = pathname.split('/').filter(Boolean)
  return parts.map((part, i) => {
    const to = '/' + parts.slice(0, i + 1).join('/')
    const last = i === parts.length - 1
    const linkable = !last && (reachable.has(to) || (role === 'admin' && /^\/accounts\/[^/]+$/.test(to)))
    return { to, last, linkable, label: crumbLabel(to, part, role, notFound, last) }
  })
}

function Breadcrumb() {
  const crumbs = useCrumbs()
  // Phones: a compact link back to the nearest reachable parent.
  const parent = [...crumbs].reverse().find((crumb) => crumb.linkable)
  return <>
    {parent && <Link to={parent.to} className="breadcrumb-back sm:hidden"><ArrowLeft size={15} aria-hidden="true" /> {parent.label}</Link>}
    <nav className="breadcrumb-trail hidden items-center gap-1.5 text-sm text-muted-foreground sm:flex" aria-label="Breadcrumb">
      {crumbs.map((crumb) => (
        <span key={crumb.to} className="flex items-center gap-1.5">
          <ChevronRight className="breadcrumb-separator" aria-hidden="true" />
          {crumb.last ? <span className="font-medium text-foreground" aria-current="page">{crumb.label}</span>
            : crumb.linkable ? <Link to={crumb.to}>{crumb.label}</Link>
            : <span>{crumb.label}</span>}
        </span>
      ))}
    </nav>
  </>
}

function AccountSwitcher() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const { username: current } = useParams()
  const { data } = useQuery({ queryKey: ['accounts'], queryFn: () => get('/api/v1/accounts'), retry: false })
  const accounts = Array.isArray(data) ? data : []
  const visible = query.trim() ? accounts.filter((a) => a.username.toLowerCase().includes(query.trim().toLowerCase())) : accounts
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" size="sm" className="hidden max-w-[200px] sm:inline-flex">
          <User className="h-4 w-4 shrink-0" />
          <span className="truncate">{current || 'Select account'}</span>
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <DropdownMenuLabel>Manage account</DropdownMenuLabel>
        <div className="px-2 pb-2" onKeyDown={(e) => e.stopPropagation()}>
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search accounts…" aria-label="Search accounts" />
        </div>
        <div className="max-h-64 overflow-y-auto">
        {accounts.length === 0 && <div className="px-2.5 py-1.5 text-sm text-muted-foreground">No accounts</div>}
        {accounts.length > 0 && visible.length === 0 && <div className="px-2.5 py-3 text-sm text-muted-foreground">No matching accounts</div>}
        {visible.map((a) => (
          <DropdownMenuItem key={a.username} onClick={() => navigate(`/accounts/${a.username}`)}>
            <span className="flex-1 truncate">{a.username}</span>
            {a.username === current && <Check className="h-4 w-4 text-accent" />}
          </DropdownMenuItem>
        ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function DomainSwitcher() {
  const username = useAccountUsername()
  const [query, setQuery] = useState('')
  const { data, isLoading } = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${encodeURIComponent(username)}/domains`),
    enabled: !!username,
    staleTime: 30_000,
    retry: false,
  })
  const domains = (data?.domains || []).filter((item) => item.kind !== 'parked').sort((left, right) => {
    const rank = { primary: 0, addon: 1, subdomain: 2 }
    return (rank[left.kind] ?? 3) - (rank[right.kind] ?? 3) || left.domain.localeCompare(right.domain)
  })
  const [domain, setDomain] = useDomainContext(username, domains)
  const needle = query.trim().toLowerCase()
  const visible = needle ? domains.filter((item) => item.domain.toLowerCase().includes(needle)) : domains

  return <DropdownMenu onOpenChange={(open) => { if (!open) setQuery('') }}>
    <DropdownMenuTrigger asChild>
      <button className="domain-context-trigger" aria-label="Choose working domain" disabled={!username || isLoading || domains.length === 0}>
        <Globe2 aria-hidden="true" />
        <span>{isLoading ? 'Loading domains…' : domain || 'No domains'}</span>
        <ChevronsUpDown aria-hidden="true" />
      </button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="end" className="domain-context-menu">
      <DropdownMenuLabel>Working domain</DropdownMenuLabel>
      <div className="domain-context-search" onKeyDown={(event) => event.stopPropagation()}>
        <Search aria-hidden="true" />
        <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter domains" aria-label="Filter domains" autoComplete="off" />
      </div>
      <div className="domain-context-list">
        {visible.map((item) => <DropdownMenuItem key={item.domain} onSelect={() => setDomain(item.domain)}>
          <span className="min-w-0 flex-1 truncate">{item.domain}</span>
          {item.domain === domain && <Check className="h-4 w-4 text-accent" />}
        </DropdownMenuItem>)}
        {!visible.length && <div className="px-3 py-4 text-sm text-muted-foreground">No matching domains</div>}
      </div>
    </DropdownMenuContent>
  </DropdownMenu>
}

export function Topbar({ inner = false }) {
  const theme = useUI((s) => s.theme)
  const mobileNavOpen = useUI((s) => s.mobileNavOpen)
  const toggleTheme = useUI((s) => s.toggleTheme)
  const { role, username, logout, identitySynced } = useAuth()
  const navigate = useNavigate()
  const isAdmin = identitySynced && role === 'admin'
  const isReseller = identitySynced && role === 'reseller'
  const home = isAdmin ? '/overview' : isReseller ? '/reseller' : '/dashboard'
  const { brandingReady, panelName, logoUrl } = useBranding()
  const skin = useUI((s) => s.skin)
  // Navy wordmark on light chrome, white wordmark on navy or dark chrome.
  const wordmark = skin === 'evolution' && theme !== 'dark' ? 'boron-logo-navy.svg' : 'boron-logo-white.svg'
  const setPaletteOpen = useUI(s => s.setPaletteOpen)
  const setMobileNavOpen = useUI(s => s.setMobileNavOpen)

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <header className="panel-topbar">
      <div className="topbar-identity">
        {inner && <button type="button" className="inner-menu-button" aria-label="Menu" aria-expanded={mobileNavOpen} aria-controls="panel-navigation-drawer" onClick={() => setMobileNavOpen(true)}><Menu aria-hidden="true" /><span>Menu</span></button>}
        <Link to={home} className="panel-brand" aria-label={`${panelName} home`}>
          {!brandingReady
            ? <span className="brand-loading-placeholder" aria-hidden="true" />
            : logoUrl
            ? <img className="custom-brand-logo" src={logoUrl} alt={panelName} />
            : <span className="boron-brand-logo"><img src={`/static/dist/brand/${wordmark}`} alt="Boron" /></span>}
        </Link>
      </div>

      <div className="topbar-actions">
        <button type="button" className={`global-search-trigger ${inner ? '' : 'dashboard-global-search'}`} aria-label="Search the panel" aria-haspopup="dialog" onClick={() => setPaletteOpen(true)}><Search aria-hidden="true" /><span>Search</span><kbd>Ctrl K</kbd></button>
        {identitySynced && !isAdmin && !isReseller && <DomainSwitcher />}
        <ThemeSelector compact />


        <button
          onClick={toggleTheme}
          className="topbar-icon-button topbar-theme-toggle"
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {theme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
        </button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" className="topbar-account" aria-label="Open account menu">
              <span className="topbar-avatar" aria-hidden="true">
                {(username || role || '?').slice(0, 2).toUpperCase()}
              </span>
              <span className="topbar-account-name">{username || role}</span>
              <ChevronDown aria-hidden="true" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuLabel>
              <div className="font-medium text-foreground">{username || 'Account'}</div>
              <div className="text-xs">{role === 'admin' ? 'Administrator' : role === 'reseller' ? 'Reseller' : 'User account'}</div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => navigate('/change-password')}>
              <KeyRound className="h-4 w-4" /> Change password
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => navigate('/security')}>
              <ShieldCheck className="h-4 w-4" /> Two-factor auth
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => navigate('/appearance')}>
              <Palette className="h-4 w-4" /> Appearance
            </DropdownMenuItem>
            <DropdownMenuItem onClick={toggleTheme}>
              {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              {theme === 'dark' ? 'Light mode' : 'Dark mode'}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem destructive onClick={handleLogout}>
              <LogOut className="h-4 w-4" /> Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  )
}

export function PageNavigation() {
  const role = useAuth((s) => s.role)
  const identitySynced = useAuth((s) => s.identitySynced)
  const { pathname } = useLocation()
  const isAdmin = identitySynced && role === 'admin'
  const home = isAdmin ? '/overview' : role === 'reseller' ? '/reseller' : '/dashboard'
  const atHome = pathname === home
  const accountDetail = isAdmin && pathname !== '/accounts/new' && /^\/accounts\/[^/]+\/?$/.test(pathname)
  return <div className="page-navigation" data-home={atHome || undefined}>
    {accountDetail ? <Link to="/accounts" className="home-link"><ArrowLeft size={15} /> Accounts</Link> : <><Link to={home} className="home-link" data-has-parent={!atHome || undefined}><Home size={15} /> Home</Link>{!atHome && <Breadcrumb />}</>}
    <div className="ml-auto">{isAdmin && !accountDetail && <AccountSwitcher />}</div>
  </div>
}
