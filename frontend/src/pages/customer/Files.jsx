import { useEffect, useState } from 'react'
import { useAccountUsername } from '@/hooks/useAccount'
import { api } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'

// File manager v2: the custom in-SPA file manager is replaced by FileBrowser
// Quantum, served at /files behind boron-api's authenticated proxy (see
// api/routers/filebrowser.py). This page just hands off — it opens the launch
// endpoint in the current tab (authorizes the account, records the audited access,
// sets the signed target cookie, lands in FileBrowser Quantum scoped to this
// account's own home). The launch is prepared in this tab so browser popup
// blocking cannot turn the customer link into a silent dashboard redirect.
// Monaco-style code editing now lives inside FileBrowser Quantum itself, so
// it's no longer bundled here.
export default function Files() {
  const username = useAccountUsername()
  const [attempt, setAttempt] = useState(0)
  const [error, setError] = useState(null)
  useEffect(() => {
    if (!username) return
    let active = true
    setError(null)
    api.get(`/api/v1/accounts/${encodeURIComponent(username)}/files/launch`, {
      headers: { Accept: 'text/html' },
      responseType: 'text',
    }).then(response => {
      if (!active) return
      const finalUrl = response.request?.responseURL || ''
      const finalPath = finalUrl ? new URL(finalUrl, window.location.origin).pathname : ''
      if (!finalPath.startsWith('/files')) throw new Error('The file manager did not return a usable session. Retry once or ask the administrator to check the File Manager service.')
      window.location.assign('/files/')
    }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason : new Error('The file manager could not start.'))
    })
    return () => { active = false }
  }, [username, attempt])

  return <div className="reference-page">
    <PageHeader title="File Manager" description="Browse, upload and edit files in your hosting account." />
    {error
      ? <ErrorState error={error} onRetry={() => setAttempt(value => value + 1)} />
      : <CenteredSpinner label="Opening file manager…" />}
  </div>
}
