import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRightLeft, Construction, ExternalLink, FileWarning, Gauge, Shield, Zap } from 'lucide-react'
import { get } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { Select } from '@/components/ui/Select'
import { Card, CardContent } from '@/components/ui/Card'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState, EmptyState } from '@/components/ui/States'
import { RedirectsTab, ForwardingTab, CacheTab, SecurityTab, MaintenanceTab, ErrorPagesTab, StatsTab } from './DomainDetail'

const features = {
  redirects: { title: 'Site Redirection', description: 'Redirect individual paths to new web addresses.', icon: ExternalLink, view: RedirectsTab },
  forwarding: { title: 'Domain Forwarding', description: 'Forward an entire domain while preserving optional URL paths.', icon: ArrowRightLeft, view: ForwardingTab },
  cache: { title: 'LiteSpeed Cache', description: 'Configure and purge the server-side cache for a website.', icon: Zap, view: CacheTab },
  security: { title: 'Website Security', description: 'Manage directory privacy, hotlink protection, and blocked visitors.', icon: Shield, view: SecurityTab },
  maintenance: { title: 'Website Maintenance', description: 'Publish a maintenance page while work is in progress.', icon: Construction, view: MaintenanceTab },
  errors: { title: 'Custom Error Pages', description: 'Create branded pages for common website errors.', icon: FileWarning, view: ErrorPagesTab },
  statistics: { title: 'Website Statistics', description: 'Review traffic, visitors, referrers, and bandwidth for a domain.', icon: Gauge, view: StatsTab },
}

export default function DomainFeature({ feature }) {
  const username = useAccountUsername()
  const definition = features[feature]
  const [domain, setDomain] = useState('')
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username,
  })
  const domains = (data?.domains || []).filter((item) => item.kind !== 'parked')
  useEffect(() => { if (!domain && domains[0]) setDomain(domains[0].domain) }, [domain, domains])
  if (!definition) return null
  const View = definition.view

  return <div className="domain-feature-page">
    <PageHeader title={definition.title} description={definition.description} icon={definition.icon}>
      {domains.length > 0 && <label className="domain-picker"><span>Domain</span><Select aria-label="Choose domain" value={domain} onChange={(event) => setDomain(event.target.value)}>{domains.map((item) => <option key={item.domain} value={item.domain}>{item.domain}</option>)}</Select></label>}
    </PageHeader>
    {isLoading && <CenteredSpinner label="Loading domains…" />}
    {error && <ErrorState title="Could not load domains" error={error} onRetry={refetch} />}
    {!isLoading && !error && domains.length === 0 && <Card><CardContent><EmptyState title="No domains yet" description="Add a domain before configuring this feature." /></CardContent></Card>}
    {domain && <div className="traditional-feature"><View username={username} domain={domain} /></div>}
  </div>
}
