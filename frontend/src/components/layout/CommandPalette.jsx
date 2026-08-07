import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { Search, CornerDownLeft, Sun, Moon, KeyRound, LogOut } from 'lucide-react'
import { cn } from '@/lib/cn'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { customerNav, adminNav } from '@/config/nav'

// Flattens the role-aware nav into palette entries, keeping section names.
function buildEntries(nav) {
  const out = []
  let section = 'Pages'
  for (const item of nav) {
    if (item.section) section = item.section
    else out.push({ type: 'page', section, label: item.label, to: item.to, icon: item.icon, external: item.external })
  }
  return out
}

// Ctrl/Cmd+K quick navigation: jump to any page or common action.
export function CommandPalette() {
  const open = useUI((s) => s.paletteOpen)
  const setOpen = useUI((s) => s.setPaletteOpen)
  const theme = useUI((s) => s.theme)
  const toggleTheme = useUI((s) => s.toggleTheme)
  const isAdmin = useAuth((s) => s.role === 'admin')
  const logout = useAuth((s) => s.logout)
  const navigate = useNavigate()

  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const listRef = useRef(null)

  const entries = useMemo(() => {
    const pages = buildEntries(isAdmin ? adminNav : customerNav)
    const actions = [
      {
        type: 'action', section: 'Actions', label: theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode',
        icon: theme === 'dark' ? Sun : Moon, run: toggleTheme,
      },
      { type: 'action', section: 'Actions', label: 'Change password', icon: KeyRound, run: () => navigate('/change-password') },
      { type: 'action', section: 'Actions', label: 'Log out', icon: LogOut, run: async () => { await logout(); navigate('/login') } },
    ]
    return [...pages, ...actions]
  }, [isAdmin, theme, toggleTheme, logout, navigate])

  const results = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return entries
    return entries.filter(
      (e) => e.label.toLowerCase().includes(q) || e.section.toLowerCase().includes(q) || (e.to || '').includes(q),
    )
  }, [entries, query])

  // Reset state whenever the palette opens or the query changes.
  useEffect(() => { setSelected(0) }, [query, open])
  useEffect(() => { if (!open) setQuery('') }, [open])

  // Keep the selected row visible while arrowing through results.
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${selected}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  function run(entry) {
    setOpen(false)
    if (entry.type === 'page' && entry.external) window.open(entry.to, '_blank', 'noopener')
    else if (entry.type === 'page') navigate(entry.to)
    else entry.run()
  }

  function onKeyDown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelected((i) => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelected((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && results[selected]) {
      e.preventDefault()
      run(results[selected])
    }
  }

  // Group consecutive results by section for header rows.
  let lastSection = null

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/50 data-[state=open]:animate-fade-in" />
        <DialogPrimitive.Content
          className="fixed left-1/2 top-[12vh] z-50 w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 overflow-hidden rounded-card border border-border bg-card shadow-dropdown focus:outline-none data-[state=open]:animate-scale-in"
          onKeyDown={onKeyDown}
        >
          <DialogPrimitive.Title className="sr-only">Search the panel</DialogPrimitive.Title>
          <div className="flex items-center gap-2.5 border-b border-border px-4">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Go to page or action…"
              className="h-12 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
            />
            <kbd className="rounded border border-border px-1.5 py-0.5 text-[10px] font-medium uppercase text-muted-foreground">esc</kbd>
          </div>
          <div ref={listRef} className="max-h-[50vh] overflow-y-auto p-2">
            {results.length === 0 && (
              <div className="px-3 py-8 text-center text-sm text-muted-foreground">
                Nothing matches "{query}". Try a page name like "domains".
              </div>
            )}
            {results.map((entry, idx) => {
              const header = entry.section !== lastSection ? entry.section : null
              lastSection = entry.section
              const Icon = entry.icon
              return (
                <div key={`${entry.section}-${entry.label}`}>
                  {header && (
                    <div className="px-3 pb-1 pt-3 text-xs font-semibold text-muted-foreground first:pt-1">{header}</div>
                  )}
                  <button
                    type="button"
                    data-idx={idx}
                    onClick={() => run(entry)}
                    onMouseMove={() => setSelected(idx)}
                    className={cn(
                      'flex w-full items-center gap-3 rounded-btn px-3 py-2 text-left text-sm transition-colors',
                      idx === selected
                        ? 'bg-accent-50 text-accent-700 dark:bg-accent-950/60 dark:text-accent-200'
                        : 'text-foreground',
                    )}
                  >
                    <Icon className={cn('h-4 w-4 shrink-0', idx === selected ? 'text-accent-600 dark:text-accent-300' : 'text-muted-foreground')} />
                    <span className="flex-1 truncate">{entry.label}</span>
                    {entry.to && <span className="font-mono text-[11px] text-muted-foreground/70">{entry.to}</span>}
                    {idx === selected && <CornerDownLeft className="h-3.5 w-3.5 shrink-0 opacity-60" />}
                  </button>
                </div>
              )
            })}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
