import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAccountUsername } from '@/hooks/useAccount'
import { CenteredSpinner } from '@/components/ui/Spinner'

// File manager v2: the custom in-SPA file manager is replaced by FileBrowser
// Quantum, served at /files behind boron-api's authenticated proxy (see
// api/routers/filebrowser.py). This page just hands off — it opens the launch
// endpoint in a new tab (authorizes the account, records the audited access,
// sets the signed target cookie, lands in FileBrowser Quantum scoped to this
// account's own home) and returns the SPA tab to the dashboard, so the panel
// stays open alongside the file manager rather than being replaced by it.
// Monaco-style code editing now lives inside FileBrowser Quantum itself, so
// it's no longer bundled here.
export default function Files() {
  const username = useAccountUsername()
  const navigate = useNavigate()
  useEffect(() => {
    if (!username) return
    window.open(`/api/v1/accounts/${username}/files/launch`, '_blank', 'noopener')
    navigate('/dashboard', { replace: true })
  }, [username, navigate])
  return <CenteredSpinner label="Opening file manager in a new tab…" />
}
