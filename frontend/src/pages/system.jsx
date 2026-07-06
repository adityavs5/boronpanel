import { Link } from 'react-router-dom'
import { ServerCrash, Compass } from 'lucide-react'
import { Button } from '@/components/ui/Button'

export function Maintenance() {
  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-4 bg-background p-6 text-center">
      <ServerCrash className="h-14 w-14 text-warning" />
      <h1 className="text-2xl font-semibold text-foreground">Under maintenance</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        The Forgehost control plane is temporarily unavailable. This page refreshes automatically — please try again in a moment.
      </p>
      <Button onClick={() => window.location.reload()}>Retry now</Button>
    </div>
  )
}

export function NotFound() {
  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-4 p-6 text-center">
      <Compass className="h-14 w-14 text-muted-foreground" />
      <h1 className="text-2xl font-semibold text-foreground">Page not found</h1>
      <p className="text-sm text-muted-foreground">The page you're looking for doesn't exist.</p>
      <Button asChild variant="secondary">
        <Link to="/">Back to panel</Link>
      </Button>
    </div>
  )
}
