import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ScrollText, RefreshCw, Server, FileCode, Search, X, Download, Activity } from 'lucide-react'
import { get } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Badge } from '@/components/ui/Badge'
import { Switch } from '@/components/ui/Toggle'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'

const LOG_TYPES = [
  { value: 'error', label: 'Web server errors', icon: Server },
  { value: 'access', label: 'Access log', icon: Activity },
  { value: 'php', label: 'PHP', icon: FileCode },
]

export default function Logs() {
  const username = useAccountUsername()
  const [type, setType] = useState('error')
  const [domain, setDomain] = useState('') // '' = primary domain (server default)
  const [severityInput, setSeverityInput] = useState('')
  const [severity, setSeverity] = useState('') // applied filter
  const [segment, setSegment] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [live, setLive] = useState(false)

  // Domains power the per-domain error log selector (OLS logs are per-vhost).
  const { data: domainsData } = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })
  const domains = domainsData?.domains || []

  const effectiveDomain = type !== 'php' ? domain : ''
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ['logs', username, type, effectiveDomain, severity, segment, fromDate, toDate],
    queryFn: () => {
      const params = new URLSearchParams()
      if (effectiveDomain) params.set('domain', effectiveDomain)
      if (severity) params.set('severity', severity)
      if (segment) params.set('segment', segment)
      if (fromDate) params.set('from_date', fromDate)
      if (toDate) params.set('to_date', toDate)
      const qs = params.toString()
      return get(`/api/v1/accounts/${username}/logs/${type}${qs ? `?${qs}` : ''}`)
    },
    enabled: !!username,
    refetchInterval: live ? 5000 : false,
  })

  const lines = data?.lines || []
  const selectedSegment = segment || data?.segment || ''
  const downloadUrl = (() => {
    const params = new URLSearchParams()
    if (effectiveDomain) params.set('domain', effectiveDomain)
    if (selectedSegment) params.set('segment', selectedSegment)
    const query = params.toString()
    return `/api/v1/accounts/${username}/logs/${type}/download${query ? `?${query}` : ''}`
  })()

  function applySeverity(e) {
    e.preventDefault()
    setSeverity(severityInput.trim())
  }

  function clearSeverity() {
    setSeverityInput('')
    setSeverity('')
  }

  return (
    <div>
      <PageHeader
        title="Logs"
        description="Review per-domain access, web-server error, and PHP logs, including compressed rotated history."
        icon={ScrollText}
      >
        <Button variant="secondary" onClick={() => refetch()} loading={isFetching}>
          <RefreshCw className="h-4 w-4" /> Refresh
        </Button>
      </PageHeader>

      <Tabs value={type} onValueChange={(value) => { setType(value); setSegment('') }}>
        <TabsList>
          {LOG_TYPES.map((t) => {
            const Icon = t.icon
            return (
              <TabsTrigger key={t.value} value={t.value}>
                <Icon className="h-4 w-4" /> {t.label}
              </TabsTrigger>
            )
          })}
        </TabsList>
      </Tabs>

      <Card className="mt-5">
        <CardContent className="space-y-4 pt-6">
          {/* Controls */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-1 flex-col gap-3 sm:flex-row sm:items-center">
              {type !== 'php' && (
                <Select
                  aria-label="Domain"
                  value={domain}
                  onChange={(e) => { setDomain(e.target.value); setSegment('') }}
                  className="sm:max-w-xs"
                >
                  <option value="">Primary domain (default)</option>
                  {domains.map((d) => (
                    <option key={d.domain} value={d.domain}>{d.domain}</option>
                  ))}
                </Select>
              )}
              <Select aria-label="Log segment" value={selectedSegment} onChange={(e) => setSegment(e.target.value)} className="sm:max-w-xs">
                {(data?.segments || []).length === 0 && <option value="">Current log</option>}
                {(data?.segments || []).map((item) => <option key={item.name} value={item.name}>{item.active ? 'Current log' : new Date(item.modified_at).toLocaleString()} · {(item.size / 1024).toFixed(1)} KiB{item.compressed ? ' · gzip' : ''}</option>)}
              </Select>
              <form onSubmit={applySeverity} className="flex flex-1 items-center gap-2">
                <div className="relative flex-1">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={severityInput}
                    onChange={(e) => setSeverityInput(e.target.value)}
                    placeholder="Filter by text (e.g. ERROR, WARN)"
                    className="pl-8"
                  />
                </div>
                <Button type="submit" variant="outline">Apply</Button>
                {severity && (
                  <Button type="button" variant="ghost" size="icon" aria-label="Clear filter" onClick={clearSeverity}>
                    <X className="h-4 w-4" />
                  </Button>
                )}
              </form>
            </div>
          </div>

          <div className="grid gap-3 border-t border-border pt-4 sm:grid-cols-[1fr_1fr_auto_auto] sm:items-end">
            <label className="space-y-1 text-sm"><span className="font-medium">From date</span><Input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} /></label>
            <label className="space-y-1 text-sm"><span className="font-medium">To date</span><Input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} /></label>
            <label className="flex h-10 items-center gap-2 rounded-btn border border-border px-3 text-sm"><Switch checked={live} onCheckedChange={setLive} aria-label="Live tail" /> Live tail</label>
            {data?.state !== 'missing' && <Button asChild variant="outline"><a href={downloadUrl}><Download className="h-4 w-4" /> Download</a></Button>}
          </div>

          {/* Meta */}
          {!isLoading && !error && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>Showing {data?.line_count ?? lines.length} matching line(s) from a bounded tail.</span>
              {data?.domain && (
                <Badge variant="neutral">{data.domain}</Badge>
              )}
              {severity && (
                <Badge variant="accent">filter: {severity}</Badge>
              )}
            </div>
          )}

          {/* Log console */}
          {isLoading ? (
            <CenteredSpinner />
          ) : error ? (
            <ErrorState error={error} onRetry={refetch} title="Could not load logs" />
          ) : data?.state === 'missing' ? (
            <div className="rounded-btn border border-dashed border-border px-4 py-14 text-center text-sm text-muted-foreground">This log file has not been created yet. It will appear after the first matching request or error.</div>
          ) : data?.state === 'empty' ? (
            <div className="rounded-btn border border-border bg-slate-950 px-4 py-14 text-center text-sm text-slate-400">The log exists and has no entries yet.</div>
          ) : lines.length === 0 ? (
            <div className="rounded-btn border border-border bg-slate-950 px-4 py-14 text-center text-sm text-slate-400">
              No matching log lines.
            </div>
          ) : (
            <pre className="max-h-[65vh] overflow-auto rounded-btn border border-border bg-slate-950 p-4 font-mono text-xs leading-relaxed text-slate-100 whitespace-pre-wrap">
              {lines.join('\n')}
            </pre>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
