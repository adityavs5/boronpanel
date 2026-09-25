import { useState } from 'react'
import { NavLink, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { Search, X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { get } from '@/lib/api'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { customerNav, adminNav, resellerNav } from '@/config/nav'
import { useBranding } from '@/hooks/useBranding'
import { Input } from '@/components/ui/Input'

export function MobileNavDrawer() {
  const open = useUI((s) => s.mobileNavOpen)
  const setOpen = useUI((s) => s.setMobileNavOpen)
  const role = useAuth((s) => s.role)
  const isAdmin = role === 'admin'
  const navigate = useNavigate()
  const { username: currentAccount } = useParams()
  const [accountQuery, setAccountQuery] = useState('')
  const accountsQuery = useQuery({
    queryKey: ['accounts'],
    queryFn: () => get('/api/v1/accounts'),
    enabled: isAdmin,
    retry: false,
  })
  const { brandingReady, panelName, logoUrl } = useBranding()
  const nav = isAdmin ? adminNav : role === 'reseller' ? resellerNav : customerNav
  const accounts = Array.isArray(accountsQuery.data) ? accountsQuery.data : []
  const matchingAccounts = (accountQuery.trim()
    ? accounts.filter((account) => account.username.toLowerCase().includes(accountQuery.trim().toLowerCase()))
    : accounts).slice(0, 6)

  function openAccount(username) {
    navigate(`/accounts/${username}`)
    setOpen(false)
    setAccountQuery('')
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/55 data-[state=open]:animate-fade-in" />
        <DialogPrimitive.Content
          className="fixed inset-y-0 left-0 z-50 flex w-[min(88vw,320px)] flex-col bg-sidebar text-white shadow-dropdown outline-none"
          aria-describedby={undefined}
        >
          <DialogPrimitive.Title className="sr-only">Panel navigation</DialogPrimitive.Title>
          <div className="flex h-16 items-center gap-3 border-b border-sidebar-border px-4">
            {!brandingReady ? <div className="h-8 w-8" aria-hidden="true" /> : logoUrl ? (
              <img src={logoUrl} alt="" className="h-8 w-8 rounded-btn object-contain" />
            ) : (
              <div className="flex h-8 w-8 items-center justify-center rounded-btn bg-accent font-bold text-accent-foreground">
                {panelName.charAt(0).toUpperCase()}
              </div>
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">{panelName}</div>
              <div className="text-xs text-sidebar-muted">{isAdmin ? 'Administration' : role === 'reseller' ? 'Reseller panel' : 'Hosting account'}</div>
            </div>
            <DialogPrimitive.Close className="rounded-btn p-2 text-sidebar-muted hover:bg-sidebar-hover hover:text-white" aria-label="Close navigation">
              <X className="h-5 w-5" />
            </DialogPrimitive.Close>
          </div>
          {isAdmin && (
            <div className="border-b border-sidebar-border p-3">
              <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-gray-400" htmlFor="mobile-account-search">
                Switch account
              </label>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                <Input
                  id="mobile-account-search"
                  value={accountQuery}
                  onChange={(event) => setAccountQuery(event.target.value)}
                  placeholder="Search accounts…"
                  className="border-sidebar-border bg-sidebar-hover pl-8 text-white placeholder:text-gray-400"
                />
              </div>
              {(accountQuery || currentAccount) && (
                <div className="mt-2 max-h-48 overflow-y-auto rounded-btn border border-sidebar-border bg-sidebar-hover p-1">
                  {matchingAccounts.map((account) => (
                    <button
                      key={account.username}
                      type="button"
                      onClick={() => openAccount(account.username)}
                      className={cn(
                        'flex min-h-11 w-full items-center rounded-btn px-2.5 text-left text-sm hover:bg-sidebar',
                        account.username === currentAccount ? 'font-medium text-accent-300' : 'text-sidebar-muted',
                      )}
                    >
                      <span className="truncate">{account.username}</span>
                    </button>
                  ))}
                  {matchingAccounts.length === 0 && <div className="px-2.5 py-3 text-sm text-sidebar-muted">No matching accounts</div>}
                </div>
              )}
            </div>
          )}
          <nav className="flex-1 overflow-y-auto px-3 py-3" aria-label="Primary navigation">
            {nav.map((item, index) => item.section ? (
              <div key={`${item.section}-${index}`} className="px-3 pb-1 pt-5 text-[11px] font-semibold uppercase tracking-wider text-gray-400 first:pt-1">
                {item.section}
              </div>
            ) : item.external ? (
              <a key={item.to} href={item.to} target="_blank" rel="noopener noreferrer"
                className="flex min-h-11 items-center gap-3 rounded-btn px-3 py-2.5 text-sm font-medium text-sidebar-muted hover:bg-sidebar-hover hover:text-white">
                <item.icon className="h-[18px] w-[18px]" /> {item.label}
              </a>
            ) : (
              <NavLink key={item.to} to={item.to} onClick={() => setOpen(false)}
                className={({ isActive }) => cn(
                  'flex min-h-11 items-center gap-3 rounded-btn px-3 py-2.5 text-sm font-medium',
                  isActive ? 'bg-accent/15 text-accent-300' : 'text-sidebar-muted hover:bg-sidebar-hover hover:text-white',
                )}>
                <item.icon className="h-[18px] w-[18px] shrink-0" />
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
