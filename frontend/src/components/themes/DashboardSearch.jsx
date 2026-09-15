import { useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, X, CornerDownLeft } from 'lucide-react'
import { useAuth } from '@/store/auth'
import { adminNav, customerNav, resellerNav } from '@/config/nav'
import { searchEntries } from '@/config/search'

function entriesFor(role) {
  const nav = role === 'admin' ? adminNav : role === 'reseller' ? resellerNav : customerNav
  let section = 'Tools'
  return nav.flatMap((item) => {
    if (item.section) { section = item.section; return [] }
    return [{ ...item, section }]
  })
}

export function DashboardSearch() {
  const role = useAuth((state) => state.role)
  const navigate = useNavigate()
  const root = useRef(null)
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState(0)
  const entries = useMemo(() => entriesFor(role), [role])
  const results = useMemo(() => query.trim() ? searchEntries(entries, query).slice(0, 9) : [], [entries, query])

  function choose(item) {
    setOpen(false)
    setQuery('')
    if (item.external) window.open(item.to, '_blank', 'noopener')
    else navigate(item.to)
  }

  function onKeyDown(event) {
    if (event.key === 'ArrowDown') {
      event.preventDefault(); setSelected((value) => Math.min(value + 1, results.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault(); setSelected((value) => Math.max(0, value - 1))
    } else if (event.key === 'Enter' && results[selected]) {
      event.preventDefault(); choose(results[selected])
    } else if (event.key === 'Escape') {
      setOpen(false); setQuery('')
    }
  }

  return (
    <div className="dashboard-search" ref={root} onBlur={(event) => {
      if (!root.current?.contains(event.relatedTarget)) setOpen(false)
    }}>
      <div className="dashboard-search-field">
        <Search aria-hidden="true" />
        <input
          aria-label="Search hosting tools"
          aria-expanded={open && !!query}
          aria-controls="dashboard-search-results"
          autoComplete="off"
          spellCheck="false"
          placeholder="Search hosting tools — try DNS editor, WordPress, redirects…"
          value={query}
          onFocus={() => setOpen(true)}
          onChange={(event) => { setQuery(event.target.value); setSelected(0); setOpen(true) }}
          onKeyDown={onKeyDown}
        />
        {query && <button type="button" aria-label="Clear search" onClick={() => { setQuery(''); setOpen(false) }}><X /></button>}
      </div>
      {open && query.trim() && (
        <div id="dashboard-search-results" className="dashboard-search-results" role="listbox">
          {results.length ? results.map((item, index) => {
            const Icon = item.icon
            return <button key={`${item.section}-${item.label}`} type="button" role="option" aria-selected={index === selected}
              onMouseEnter={() => setSelected(index)} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(item)}>
              <span className="search-result-icon"><Icon /></span>
              <span><strong>{item.label}</strong><small>{item.section}</small></span>
              {index === selected && <CornerDownLeft className="search-enter" />}
            </button>
          }) : <div className="dashboard-search-empty">No matching tool. Try a related name such as “zone editor” or “cache”.</div>}
        </div>
      )}
    </div>
  )
}
