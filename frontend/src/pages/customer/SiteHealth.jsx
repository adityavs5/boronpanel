import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, CheckCircle2, CircleHelp, TriangleAlert, XCircle } from 'lucide-react'
import { get } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { useDomainContext } from '@/hooks/useDomainContext'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Select } from '@/components/ui/Select'
import { Badge } from '@/components/ui/Badge'
import { ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'

const STATUS = {
  pass: { label: 'Passed', variant: 'success', icon: CheckCircle2 },
  warn: { label: 'Needs attention', variant: 'warning', icon: TriangleAlert },
  fail: { label: 'Problem found', variant: 'danger', icon: XCircle },
  unknown: { label: 'Uncertain', variant: 'info', icon: CircleHelp },
}

export default function SiteHealth() {
  const username = useAccountUsername()
  const domainsQuery = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })
  const domains = useMemo(() => (domainsQuery.data?.domains || []).map(row => row.domain), [domainsQuery.data])
  const [domain, setDomain] = useDomainContext(username, domains)
  const query = useQuery({
    queryKey: ['site-health', username, domain],
    queryFn: () => get(`/api/v1/accounts/${username}/site-health${domain ? `?domain=${encodeURIComponent(domain)}` : ''}`),
    enabled: !!username && domains.length > 0,
    refetchOnWindowFocus: false,
  })

  return <div>
    <PageHeader
      title="Why is my site down?"
      description="Read-only checks for DNS, TLS, web service, PHP, logs, limits, and mail. Boron makes no changes."
      icon={Activity}
      primaryAction={<Button loading={query.isFetching} disabled={!domains.length} onClick={() => query.refetch()}>Run checks</Button>}
    />
    <Card className="mb-5"><CardContent className="flex flex-wrap items-end gap-4 py-4"><label className="grid min-w-[16rem] gap-1.5 text-sm font-medium">Domain<Select value={domain || query.data?.domain || ''} onChange={event => setDomain(event.target.value)} disabled={!domains.length}>{domains.map(item => <option key={item} value={item}>{item}</option>)}</Select></label><p className="pb-2 text-sm text-muted-foreground">Results describe observed evidence. Uncertain checks never recommend changing DNS or certificates.</p></CardContent></Card>
    {domainsQuery.isLoading || query.isLoading ? <CardSkeleton /> : domainsQuery.error ? <ErrorState error={domainsQuery.error} onRetry={domainsQuery.refetch} /> : !domains.length ? <Card><CardContent className="py-8 text-center"><p className="font-medium">No domain is available</p><p className="mt-1 text-sm text-muted-foreground">Create a domain before running site checks.</p></CardContent></Card> : query.error ? <ErrorState error={query.error} onRetry={query.refetch} /> : <>
      <div className="mb-4 flex items-center gap-3"><Badge variant={STATUS[query.data?.overall]?.variant || 'info'}>{STATUS[query.data?.overall]?.label || 'Check complete'}</Badge><span className="text-sm text-muted-foreground">{query.data?.domain}</span></div>
      <div className="divide-y divide-border border-y border-border">{(query.data?.findings || []).map(finding => <Finding key={finding.name} finding={finding} />)}</div>
    </>}
  </div>
}

function Finding({ finding }) {
  const meta = STATUS[finding.status] || STATUS.unknown
  const Icon = meta.icon
  return <section className="grid gap-3 py-4 md:grid-cols-[2rem_11rem_9rem_1fr] md:items-start">
    <Icon className={`mt-0.5 h-5 w-5 ${finding.status === 'pass' ? 'text-success' : finding.status === 'fail' ? 'text-danger' : finding.status === 'warn' ? 'text-warning' : 'text-info'}`} />
    <strong className="text-sm">{finding.name}</strong>
    <div><Badge variant={meta.variant}>{meta.label}</Badge><p className="mt-1 text-xs capitalize text-muted-foreground">{finding.confidence} confidence</p></div>
    <div><p className="text-sm">{finding.explanation}</p><p className="mt-1 text-sm text-muted-foreground">Next step: {finding.next_step}</p></div>
  </section>
}
