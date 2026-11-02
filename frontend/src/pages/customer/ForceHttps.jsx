import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock } from 'lucide-react'
import { get, put } from '@/lib/api'
import { Switch } from '@/components/ui/Toggle'
import { Skeleton } from '@/components/ui/Skeleton'
import { InlineError, InlineWarning } from './InlineFeedback'

// Per-domain "Force HTTPS" (GET/PUT .../domains/{d}/force-https): a permanent
// redirect from http:// to https:// for the names the domain's certificate
// covers. It can only be turned on while a valid certificate exists; once on,
// `reason` explains why it is not in effect (no certificate any more,
// forwarding, maintenance mode, suspension). The ['force-https'] query is
// invalidated when a certificate is issued.
export function ForceHttpsSetting({ username, domain }) {
  const qc = useQueryClient()
  const key = ['force-https', username, domain]
  const url = `/api/v1/accounts/${encodeURIComponent(username)}/domains/${encodeURIComponent(domain)}/force-https`
  const { data, isLoading, error } = useQuery({ queryKey: key, queryFn: () => get(url), enabled: !!username && !!domain })
  const save = useMutation({
    mutationFn: enabled => put(url, { enabled }),
    onSuccess: result => qc.setQueryData(key, result),
  })
  const switchId = `force-https-${domain}`
  if (isLoading) return <Skeleton className="h-16 w-full" />
  if (error) return <InlineError error={error} />
  if (!data) return null
  // Turning it off always works; turning it on needs a valid certificate.
  const blocked = !data.enabled && !data.certificate_valid
  const covered = data.covered_hosts || [], uncovered = data.uncovered_hosts || []
  return (
    <section aria-labelledby={`${switchId}-label`} className="space-y-3 rounded-btn border border-border p-3 text-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p id={`${switchId}-label`} className="flex items-center gap-2 font-medium text-foreground"><Lock className="h-4 w-4 shrink-0" aria-hidden="true" /> Force HTTPS</p>
          <p id={`${switchId}-help`} className="mt-0.5 text-muted-foreground">Redirect every http:// request to https:// (permanent redirect).</p>
        </div>
        <Switch id={switchId} aria-labelledby={`${switchId}-label`} aria-describedby={`${switchId}-help`} checked={!!data.enabled} disabled={blocked || save.isPending} onCheckedChange={value => save.mutate(value)} />
      </div>
      {blocked && <p className="text-muted-foreground">Issue an SSL certificate for {data.domain} first.</p>}
      {data.active && covered.length > 0 && <p className="break-words text-foreground">http:// is redirected to https:// for: <span className="font-mono">{covered.join(', ')}</span></p>}
      {data.enabled && uncovered.map(host => <p key={host} className="break-words text-muted-foreground"><span className="font-mono">{host}</span> is not on the certificate, so it stays on http://</p>)}
      {data.enabled && !data.active && data.reason && <InlineWarning><p>{data.reason}</p></InlineWarning>}
      <InlineError error={!save.isPending && save.error} />
    </section>
  )
}
