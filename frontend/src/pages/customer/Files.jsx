import { useEffect } from 'react'
import { useAccountUsername } from '@/hooks/useAccount'
import { CenteredSpinner } from '@/components/ui/Spinner'

// File manager v2: the custom in-SPA file manager is replaced by FileBrowser
// Quantum, served at /files behind boron-api's authenticated proxy (see
// api/routers/filebrowser.py). This page just hands off — it navigates to the
// launch endpoint, which authorizes the account, records the audited access,
// sets the signed target cookie, and lands the browser in FileBrowser Quantum
// scoped to this account's own home. Monaco-style code editing now lives inside
// FileBrowser Quantum itself, so it's no longer bundled here.
export default function Files() {
  const username = useAccountUsername()
  useEffect(() => {
    if (username) window.location.assign(`/api/v1/accounts/${username}/files/launch`)
  }, [username])
  return <CenteredSpinner label="Opening file manager…" />
}
