import { useState } from 'react'
import { useNavigate, useLocation, Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Palette, Home, Sun, Moon, LogOut, ChevronDown, User, KeyRound, Check, ChevronsUpDown, ShieldCheck, Globe2, Search, Menu } from 'lucide-react'
import { ThemeSelector } from '@/components/themes/ThemeSelector'
import { useBranding } from '@/hooks/useBranding'
import { get } from '@/lib/api'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { titleCase } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useAccountUsername } from '@/hooks/useAccount'
import { useDomainContext } from '@/hooks/useDomainContext'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'

function Breadcrumb() {
  const { pathname } = useLocation()
  const parts = pathname.split('/').filter(Boolean)
  return (
    <nav className="hidden items-center gap-1.5 text-sm text-muted-foreground sm:flex" aria-label="Breadcrumb">
      {parts.map((part, i) => {
        const to = '/' + parts.slice(0, i + 1).join('/')
        const last = i === parts.length - 1
        return (
          <span key={to} className="flex items-center gap-1.5">
            {i > 0 && <span className="text-border">/</span>}
            {last ? (
              <span className="font-medium text-foreground">{({wordpress:'WordPress',dns:'DNS',ssl:'SSL',php:'PHP',ftp:'FTP',ssh:'SSH'})[part] || titleCase(part)}</span>
            ) : (
              <Link to={to} className="hover:text-foreground transition-colors">
                {({wordpress:'WordPress',dns:'DNS',ssl:'SSL',php:'PHP',ftp:'FTP',ssh:'SSH'})[part] || titleCase(part)}
              </Link>
            )}
          </span>
        )
      })}
    </nav>
  )
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
  const toggleTheme = useUI((s) => s.toggleTheme)
  const { role, username, logout } = useAuth()
  const navigate = useNavigate()
  const isAdmin = role === 'admin'
  const isReseller = role === 'reseller'
  const home = isAdmin ? '/overview' : isReseller ? '/reseller' : '/dashboard'
  const { brandingReady, panelName, logoUrl } = useBranding()
  const setPaletteOpen = useUI(s => s.setPaletteOpen)
  const setMobileNavOpen = useUI(s => s.setMobileNavOpen)

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <header className="panel-topbar">
      <div className="flex items-center gap-3 min-w-0">
        {inner && <button type="button" className="inner-menu-button" aria-label="Menu" onClick={() => setMobileNavOpen(true)}><Menu aria-hidden="true" /></button>}
        <Link to={home} className="panel-brand" aria-label={`${panelName} home`}>
          {!brandingReady
            ? <span className="brand-loading-placeholder" aria-hidden="true" />
            : logoUrl
            ? <img className="custom-brand-logo" src={logoUrl} alt={panelName} />
            : <span className="boron-brand-logo"><img src="/static/dist/brand/boron-logo-source.png" alt="Boron" /></span>}
        </Link>
      </div>

      <div className="flex items-center gap-2">
        <button type="button" className={`global-search-trigger ${inner ? '' : 'dashboard-global-search'}`} aria-label="Search the panel" aria-haspopup="dialog" onClick={() => setPaletteOpen(true)}><Search aria-hidden="true" /><span>Search tools and settings</span><kbd>Ctrl K</kbd></button>
        {isAdmin || isReseller
          ? <span className="access-level"><span>Access Level</span><strong>{isAdmin ? 'Admin' : 'Reseller'}</strong></span>
          : <DomainSwitcher />}
        <ThemeSelector />


        <button
          onClick={toggleTheme}
          className="rounded-btn p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {theme === 'dark' ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
        </button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="flex items-center gap-2 rounded-btn px-2 py-1.5 transition-colors hover:bg-muted" aria-label="Open account menu">
              <div className="flex h-7 w-7 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
                {(username || role || '?').slice(0, 2).toUpperCase()}
              </div>
              <span className="hidden text-sm font-medium text-foreground sm:inline">{username || role}</span>
              <ChevronDown className="hidden h-4 w-4 text-muted-foreground sm:inline" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuLabel>
              <div className="font-medium text-foreground">{username || 'Account'}</div>
              <div className="text-xs capitalize">{role}</div>
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
  const isAdmin = role === 'admin'
  return <div className="page-navigation">
    <Link to={isAdmin ? '/overview' : role === 'reseller' ? '/reseller' : '/dashboard'} className="home-link"><Home size={15} /> Home</Link>
    <Breadcrumb />
    <div className="ml-auto">{isAdmin && <AccountSwitcher />}</div>
  </div>
}
