import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { FileLock, FolderOpen } from 'lucide-react'
import { useAccountUsername } from '@/hooks/useAccount'
import { api, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Input, Textarea, FormField } from '@/components/ui/Input'
import { Checkbox } from '@/components/ui/Toggle'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
} from '@/components/ui/Dialog'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'
import { InlineError } from './InlineFeedback'

// File manager v2: the custom in-SPA file manager is replaced by FileBrowser
// Quantum, served at /files behind boron-api's authenticated proxy (see
// api/routers/filebrowser.py). This page just hands off — it opens the launch
// endpoint in the current tab (authorizes the account, records the audited access,
// sets the signed target cookie, lands in FileBrowser Quantum scoped to this
// account's own home). The launch is prepared in this tab so browser popup
// blocking cannot turn the customer link into a silent dashboard redirect.
// Monaco-style code editing now lives inside FileBrowser Quantum itself, so
// it's no longer bundled here.
//
// FileBrowser Quantum has no permission editor (and cannot be changed), so
// /files?tool=permissions opens the panel's own "Change permissions" dialog
// instead of launching.

const linesToList = (text) => (text || '').split('\n').map((line) => line.trim()).filter(Boolean)
const MODE_RE = /^[0-7]{3}$/
const WHO = [['Owner', 0], ['Group', 1], ['Public', 2]]
const BITS = [['Read', 4], ['Write', 2], ['Execute', 1]]

function PermissionsDialog({ username, open, onOpenChange }) {
  const [paths, setPaths] = useState('')
  const [mode, setMode] = useState('644')
  const changeMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${encodeURIComponent(username)}/files/permissions`, { paths: linesToList(paths), mode }),
    onSuccess: (result) => toast.success('Permissions changed', `${result.changed?.length || 0} item${result.changed?.length === 1 ? '' : 's'} set to ${result.mode}.`),
    onError: (error) => toast.error('Could not change permissions', error.message),
  })
  const resetChange = changeMut.reset
  useEffect(() => { if (!open) resetChange() }, [open, resetChange])

  const validMode = MODE_RE.test(mode)
  const digits = (validMode ? mode : '000').split('').map(Number)
  const toggle = (who, bit, checked) => {
    const next = [...digits]
    next[who] = checked ? next[who] | bit : next[who] & ~bit
    setMode(next.join(''))
  }
  const count = linesToList(paths).length
  const changed = changeMut.data?.changed || []

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!changeMut.isPending) onOpenChange(next) }}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Change permissions</DialogTitle>
          <DialogDescription>Set the read, write and execute permissions of files and folders in your home directory.</DialogDescription>
        </DialogHeader>
        <form onSubmit={(event) => { event.preventDefault(); if (validMode && count && !changeMut.isPending) changeMut.mutate() }}>
          <DialogBody className="space-y-4">
            <FormField label="Files and folders" htmlFor="permission-paths" required hint="One per line, as the file manager shows them, for example /public_html/index.php. Up to 500.">
              <Textarea id="permission-paths" rows={4} value={paths} onChange={(event) => { setPaths(event.target.value); if (changeMut.error) changeMut.reset() }} placeholder={'/public_html/index.php\n/public_html/uploads'} required />
            </FormField>
            <FormField label="Permissions" htmlFor="permission-mode" required error={mode && !validMode ? 'Enter three digits from 0 to 7, for example 644 or 755.' : undefined} hint="Three octal digits. Files usually use 644 and folders 755.">
              <div className="flex flex-wrap items-center gap-2">
                <Input id="permission-mode" className="w-24 font-mono" inputMode="numeric" maxLength={3} value={mode} onChange={(event) => setMode(event.target.value.replace(/\s/g, ''))} required />
                <Button type="button" size="sm" variant="secondary" onClick={() => setMode('644')}>644 files</Button>
                <Button type="button" size="sm" variant="secondary" onClick={() => setMode('755')}>755 folders</Button>
              </div>
            </FormField>
            <fieldset className="rounded-btn border border-border p-3">
              <legend className="px-1 text-[13px] font-medium text-foreground">Who can do what</legend>
              <div className="grid grid-cols-[minmax(0,1fr)_repeat(3,minmax(0,auto))] items-center gap-x-4 gap-y-2 text-sm">
                <span aria-hidden="true" />
                {BITS.map(([name]) => <span key={name} aria-hidden="true" className="text-center text-xs text-muted-foreground">{name}</span>)}
                {WHO.map(([who, index]) => (
                  <div key={who} className="contents">
                    <span className="text-foreground">{who}</span>
                    {BITS.map(([name, bit]) => (
                      <span key={name} className="flex justify-center">
                        <Checkbox aria-label={`${who}: ${name.toLowerCase()}`} checked={(digits[index] & bit) === bit} onCheckedChange={(checked) => toggle(index, bit, checked === true)} />
                      </span>
                    ))}
                  </div>
                ))}
              </div>
            </fieldset>
            <p className="text-sm text-muted-foreground">Special bits (setuid, setgid and sticky) cannot be set, and the home directory itself keeps the permissions the panel manages.</p>
            <InlineError error={changeMut.error} />
            {changed.length > 0 && (
              <div className="settings-notice" role="status">
                <p className="font-medium">Set to {changeMut.data.mode}</p>
                <ul className="mt-1 space-y-0.5">{changed.map((item) => <li key={item.path} className="break-all font-mono text-xs">{item.path}</li>)}</ul>
              </div>
            )}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="secondary" disabled={changeMut.isPending} onClick={() => onOpenChange(false)}>Close</Button>
            <Button type="submit" loading={changeMut.isPending} disabled={!validMode || !count}>Change permissions</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default function Files() {
  const username = useAccountUsername()
  const [searchParams] = useSearchParams()
  const permissionsTool = searchParams.get('tool') === 'permissions'
  const [permissionsOpen, setPermissionsOpen] = useState(permissionsTool)
  const [attempt, setAttempt] = useState(0)
  const [error, setError] = useState(null)
  useEffect(() => { if (permissionsTool) setPermissionsOpen(true) }, [permissionsTool])
  useEffect(() => {
    if (!username || permissionsTool) return
    let active = true
    setError(null)
    // The default (JSON) response type: an error body is parsed, so the API's
    // path-free detail is shown instead of the raw response text. The HTML of
    // a successful launch stays a string.
    api.get(`/api/v1/accounts/${encodeURIComponent(username)}/files/launch`, {
      headers: { Accept: 'text/html' },
    }).then(response => {
      if (!active) return
      const finalUrl = response.request?.responseURL || ''
      const finalPath = finalUrl ? new URL(finalUrl, window.location.origin).pathname : ''
      if (!finalPath.startsWith('/files')) throw new Error('The file manager did not return a usable session. Retry once or ask the administrator to check the File Manager service.')
      // Replace this page in history: Back from the file manager returns to
      // the previous panel page instead of launching it again.
      window.location.replace('/files/')
    }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason : new Error('The file manager could not start.'))
    })
    return () => { active = false }
  }, [username, attempt, permissionsTool])

  return <div className="reference-page">
    <PageHeader title="File manager" description="Browse, upload and edit files in your hosting account." icon={FolderOpen}
      primaryAction={permissionsTool
        ? <Button asChild><Link to="/files"><FolderOpen className="h-4 w-4" /> Open file manager</Link></Button>
        : <Button asChild variant="secondary"><Link to="/files?tool=permissions"><FileLock className="h-4 w-4" /> Change permissions</Link></Button>} />
    {permissionsTool ? (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><FileLock className="h-4 w-4" /> File permissions</CardTitle>
          <CardDescription>The file manager cannot change permissions. Set them here for files and folders in your home directory.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button onClick={() => setPermissionsOpen(true)}><FileLock className="h-4 w-4" /> Change permissions</Button>
        </CardContent>
      </Card>
    ) : error
      ? <ErrorState error={error} onRetry={() => setAttempt(value => value + 1)} />
      : <CenteredSpinner label="Opening file manager…" />}
    {username && <PermissionsDialog username={username} open={permissionsTool && permissionsOpen} onOpenChange={setPermissionsOpen} />}
  </div>
}
