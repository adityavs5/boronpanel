import { useNavigate, useLocation, Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Menu, Search, Sun, Moon, LogOut, ChevronDown, User, KeyRound, Check, ChevronsUpDown, ShieldCheck } from 'lucide-react'
import { get } from '@/lib/api'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { titleCase } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

// Opens the Ctrl/Cmd+K command palette: a full search pill on md+, icon below.
function SearchTrigger() {
  const setPaletteOpen = useUI((s) => s.setPaletteOpen)
  return (
    <>
      <button
        type="button"
        onClick={() => setPaletteOpen(true)}
        className="hidden h-8 w-56 items-center gap-2 rounded-btn border border-border bg-surface px-2.5 text-sm text-muted-foreground transition-colors hover:border-accent/60 hover:text-foreground md:flex"
      >
        <Search className="h-3.5 w-3.5 shrink-0" />
        <span className="flex-1 text-left">Search…</span>
        <kbd className="rounded border border-border px-1 py-px text-[10px] font-medium uppercase text-muted-foreground">
          {isMac ? '⌘K' : 'Ctrl K'}
        </kbd>
      </button>
      <button
        type="button"
        onClick={() => setPaletteOpen(true)}
        className="rounded-btn p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:hidden"
        title="Search"
      >
        <Search className="h-[18px] w-[18px]" />
      </button>
    </>
  )
}

function Breadcrumb() {
  const { pathname } = useLocation()
  const parts = pathname.split('/').filter(Boolean)
  return (
    <nav className="hidden items-center gap-1.5 text-sm text-muted-foreground sm:flex">
      {parts.map((part, i) => {
        const to = '/' + parts.slice(0, i + 1).join('/')
        const last = i === parts.length - 1
        return (
          <span key={to} className="flex items-center gap-1.5">
            {i > 0 && <span className="text-border">/</span>}
            {last ? (
              <span className="font-medium text-foreground">{titleCase(part)}</span>
            ) : (
              <Link to={to} className="hover:text-foreground transition-colors">
                {titleCase(part)}
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
  const { username: current } = useParams()
  const { data } = useQuery({ queryKey: ['accounts'], queryFn: () => get('/api/v1/accounts'), retry: false })
  const accounts = Array.isArray(data) ? data : []
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" size="sm" className="max-w-[200px]">
          <User className="h-4 w-4 shrink-0" />
          <span className="truncate">{current || 'Select account'}</span>
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 overflow-y-auto">
        <DropdownMenuLabel>Manage account</DropdownMenuLabel>
        {accounts.length === 0 && <div className="px-2.5 py-1.5 text-sm text-muted-foreground">No accounts</div>}
        {accounts.map((a) => (
          <DropdownMenuItem key={a.username} onClick={() => navigate(`/accounts/${a.username}`)}>
            <span className="flex-1 truncate">{a.username}</span>
            {a.username === current && <Check className="h-4 w-4 text-accent" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function Topbar() {
  const toggleSidebar = useUI((s) => s.toggleSidebar)
  const theme = useUI((s) => s.theme)
  const toggleTheme = useUI((s) => s.toggleTheme)
  const { role, username, logout } = useAuth()
  const navigate = useNavigate()
  const isAdmin = role === 'admin'

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-border bg-surface px-4 lg:px-6">
      <div className="flex items-center gap-3 min-w-0">
        <button onClick={toggleSidebar} className="rounded-btn p-2 text-muted-foreground hover:bg-muted lg:hidden">
          <Menu className="h-5 w-5" />
        </button>
        <Breadcrumb />
      </div>

      <div className="flex items-center gap-2">
        <SearchTrigger />
        {isAdmin && <AccountSwitcher />}

        <button
          onClick={toggleTheme}
          className="rounded-btn p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {theme === 'dark' ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
        </button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="flex items-center gap-2 rounded-btn px-2 py-1.5 transition-colors hover:bg-muted">
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
