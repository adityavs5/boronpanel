import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, FlaskConical, RefreshCw, Trash2 } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { ErrorState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'
import { InlineError, InlineWarning } from './InlineFeedback'

// A staging copy of the site on staging.<domain> (.../domains/{d}/staging).
// GET answers {exists: false} when there is none. Create and sync may add a
// `notice` (the copy uses http:// until staging.<domain> points here and has
// a certificate); delete may list `cleanup_failed`.
const ALREADY_EXISTS = /staging environment for .* already exists/i
const FOLDER_IN_USE = /already contains files/i

export function StagingCard({ username, domain, admin = false }) {
  const qc = useQueryClient()
  const key = ['staging', username, domain]
  const url = `/api/v1/accounts/${encodeURIComponent(username)}/domains/${encodeURIComponent(domain)}/staging`
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [outcome, setOutcome] = useState(null) // {notice?, cleanup?: []}
  const { data, isLoading, error, refetch } = useQuery({ queryKey: key, queryFn: () => get(url), enabled: !!username && !!domain })
  const refresh = () => qc.invalidateQueries({ queryKey: key })
  const finished = (result, title) => {
    setOutcome(result?.notice ? { notice: result.notice } : null)
    toast.success(title, result?.staging_url)
    refresh()
  }
  const create = useMutation({ mutationFn: () => post(url, {}), onSuccess: result => finished(result, 'Staging site created'), onError: refresh })
  const sync = useMutation({ mutationFn: () => post(`${url}/sync`, {}), onSuccess: result => finished(result, 'Staging site synced'), onError: refresh })
  const remove = useMutation({
    mutationFn: () => del(url),
    onSuccess: result => {
      setConfirmDelete(false)
      setOutcome(result?.cleanup_failed?.length ? { cleanup: result.cleanup_failed } : null)
      if (!result?.cleanup_failed?.length) toast.success('Staging site deleted')
      refresh()
    },
  })
  const reset = () => { create.reset(); sync.reset(); remove.reset() }
  const busy = create.isPending || sync.isPending || remove.isPending
  const failure = (!busy && (create.error || sync.error)) || null
  const exists = !!data?.exists

  return (
    <Card>
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2"><FlaskConical className="h-4 w-4" /> Staging site</CardTitle>
          <CardDescription>A private copy of this website on staging.{domain}, with its own database, to try changes before they reach the live site.</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {isLoading ? <CardSkeleton /> : error ? <ErrorState error={error} onRetry={refetch} /> : <>
          {exists ? (
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2">
              <dt className="text-muted-foreground">Address</dt>
              <dd className="break-all"><a className="inline-flex items-center gap-1 rounded-sm text-link underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/30" href={data.staging_url} target="_blank" rel="noopener noreferrer">{data.staging_url} <ExternalLink className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /></a></dd>
              <dt className="text-muted-foreground">Database</dt><dd className="break-all font-mono">{data.db_name || '—'}</dd>
              <dt className="text-muted-foreground">Last synced</dt><dd>{formatDate(data.last_synced_at || data.created_at)}</dd>
            </dl>
          ) : <p className="text-muted-foreground">This website has no staging site.</p>}
          {outcome?.notice && <p role="status" className="break-words rounded-btn border border-info/30 bg-info/10 px-3 py-2 text-foreground">{outcome.notice}</p>}
          {outcome?.cleanup && (
            <InlineWarning title="The staging site was deleted, but some parts could not be removed">
              <ul className="list-disc pl-5">{outcome.cleanup.map(item => <li key={item}>{item}</li>)}</ul>
              <p>Ask your administrator to remove them.</p>
            </InlineWarning>
          )}
          {failure && (
            <InlineError>
              <p>{failure.message}</p>
              {ALREADY_EXISTS.test(failure.message) && <Button className="mt-2" size="sm" variant="secondary" onClick={() => { reset(); sync.mutate() }}><RefreshCw className="h-4 w-4" /> Sync the staging site</Button>}
              {FOLDER_IN_USE.test(failure.message) && (admin
                ? <a className="mt-2 inline-block rounded-sm font-medium underline focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/30" href={`/api/v1/accounts/${encodeURIComponent(username)}/files/launch`} target="_blank" rel="noopener noreferrer">Open the File Manager</a>
                : <Link className="mt-2 inline-block rounded-sm font-medium underline focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/30" to="/files">Open the File Manager</Link>)}
            </InlineError>
          )}
          {(create.isPending || sync.isPending) && <p role="status" className="text-muted-foreground">Copying the website files and database… this can take several minutes. Do not close this page.</p>}
          <div className="flex flex-wrap gap-2">
            {exists ? <>
              <Button variant="secondary" loading={sync.isPending} disabled={busy} onClick={() => { reset(); setOutcome(null); sync.mutate() }}><RefreshCw className="h-4 w-4" /> Sync from live site</Button>
              <Button variant="danger-ghost" disabled={busy} onClick={() => { reset(); setConfirmDelete(true) }}><Trash2 className="h-4 w-4" /> Delete staging site</Button>
            </> : <Button loading={create.isPending} disabled={busy} onClick={() => { reset(); setOutcome(null); create.mutate() }}><FlaskConical className="h-4 w-4" /> Create staging site</Button>}
          </div>
        </>}
      </CardContent>
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={value => { if (!remove.isPending) setConfirmDelete(value) }}
        title="Delete the staging site?"
        description="Delete the staging site? Its domain, database and copied files are deleted. Your live site is not changed."
        confirmLabel="Delete staging site"
        loading={remove.isPending}
        error={remove.error}
        onConfirm={() => remove.mutate()}
      />
    </Card>
  )
}
