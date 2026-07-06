import { Construction } from 'lucide-react'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/States'

// Temporary stand-in for pages not yet implemented, so routing/nav is complete
// while pages are filled in.
export default function Placeholder({ title = 'Page', description }) {
  return (
    <div>
      <PageHeader title={title} description={description} />
      <Card>
        <CardContent>
          <EmptyState icon={Construction} title="Coming soon" description={`The ${title} page is being built.`} />
        </CardContent>
      </Card>
    </div>
  )
}
