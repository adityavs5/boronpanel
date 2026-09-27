import { useCallback, useEffect, useMemo } from 'react'
import { useUI } from '@/store/ui'

// One domain context follows a hosting account around the customer panel.
// Pages still expose their normal selector, but changing either that selector
// or the top-bar selector updates the same persisted browser preference.
export function useDomainContext(username, domains = []) {
  const stored = useUI((state) => username ? state.domainContexts[username] : '')
  const setStored = useUI((state) => state.setDomainContext)
  const names = useMemo(() => domains.map((item) => typeof item === 'string' ? item : item?.domain).filter(Boolean), [domains])
  const signature = names.join('\0')
  const selected = names.includes(stored) ? stored : (names[0] || '')

  useEffect(() => {
    if (username && selected && selected !== stored) setStored(username, selected)
  }, [username, selected, stored, setStored, signature])

  const setSelected = useCallback((domain) => {
    if (username && domain && names.includes(domain)) setStored(username, domain)
  }, [username, setStored, signature])

  return [selected, setSelected]
}
