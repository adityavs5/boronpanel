import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Construction, ExternalLink } from 'lucide-react'
import { get } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/States'

// Missing-features batch, goal feature 2: admin overview of every domain
// currently in maintenance mode.
export default function Maintenance() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-maintenance'],
    queryFn: () => get('/api/v1/admin/maintenance'),
  })

  return (
    <div>
      <PageHeader title="Maintenance Mode" description="Every domain currently showing a maintenance page." icon={Construction} />
      {isLoading ? <CardSkeleton /> : error ? <ErrorState error={error} onRetry={refetch} /> : !data.domains.length ? (
        <EmptyState icon={Construction} title="No domains in maintenance" description="Nothing is currently showing a maintenance page." />
      ) : (
        <div className="space-y-3">
          {data.domains.map((d) => (
            <Card key={d.domain}>
              <CardContent className="flex items-center justify-between gap-4 py-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-foreground">{d.domain}</span>
                    <Badge variant="warning">maintenance</Badge>
                    {d.username && <Badge variant="outline">{d.username}</Badge>}
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{d.title} — {d.message}</p>
                  {d.auto_disable_at && (
                    <p className="mt-1 text-xs text-muted-foreground">Auto-disables at {new Date(d.auto_disable_at).toLocaleString()}</p>
                  )}
                </div>
                {d.username && (
                  <Link to={`/accounts/${d.username}/domains/${d.domain}`} className="inline-flex items-center gap-1 text-sm text-accent hover:underline">
                    Manage <ExternalLink className="h-3.5 w-3.5" />
                  </Link>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
