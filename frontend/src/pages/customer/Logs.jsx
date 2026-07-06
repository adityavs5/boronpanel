import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ScrollText, RefreshCw, Server, FileCode, Search, X } from 'lucide-react'
import { get } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Badge } from '@/components/ui/Badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'

const LOG_TYPES = [
  { value: 'ols', label: 'Web server (OLS)', icon: Server },
  { value: 'php', label: 'PHP', icon: FileCode },
]

export default function Logs() {
  const username = useAccountUsername()
  const [type, setType] = useState('ols')
  const [domain, setDomain] = useState('') // '' = primary domain (server default)
  const [severityInput, setSeverityInput] = useState('')
  const [severity, setSeverity] = useState('') // applied filter

  // Domains power the per-domain error log selector (OLS logs are per-vhost).
  const { data: domainsData } = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })
  const domains = domainsData?.domains || []

  const effectiveDomain = type === 'ols' ? domain : ''
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ['logs', username, type, effectiveDomain, severity],
    queryFn: () => {
      const params = new URLSearchParams()
      if (effectiveDomain) params.set('domain', effectiveDomain)
      if (severity) params.set('severity', severity)
      const qs = params.toString()
      return get(`/api/v1/accounts/${username}/logs/${type}${qs ? `?${qs}` : ''}`)
    },
    enabled: !!username,
  })

  const lines = data?.lines || []

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
        description="Tail your account's web server and PHP error logs. Only your own log files are ever shown; up to the last 500 lines are read."
        icon={ScrollText}
      >
        <Button variant="secondary" onClick={() => refetch()} loading={isFetching}>
          <RefreshCw className="h-4 w-4" /> Refresh
        </Button>
      </PageHeader>

      <Tabs value={type} onValueChange={setType}>
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
              {type === 'ols' && (
                <Select
                  aria-label="Domain"
                  value={domain}
                  onChange={(e) => setDomain(e.target.value)}
                  className="sm:max-w-xs"
                >
                  <option value="">Primary domain (default)</option>
                  {domains.map((d) => (
                    <option key={d.domain} value={d.domain}>{d.domain}</option>
                  ))}
                </Select>
              )}
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

          {/* Meta */}
          {!isLoading && !error && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>Showing last {data?.line_count ?? lines.length} matching line(s) of up to 500 read.</span>
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
