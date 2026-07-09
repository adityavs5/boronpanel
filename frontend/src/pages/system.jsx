import { Link, useRouteError } from 'react-router-dom'
import { ServerCrash, Compass, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/Button'

// Route-level error boundary: a crash in one page renders here while the
// rest of the panel (sidebar, topbar) stays usable. Set via errorElement.
export function RouteError() {
  const error = useRouteError()
  const message = error?.statusText || error?.message || 'The page hit an unexpected error.'
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-4 p-6 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-danger/10 text-danger">
        <AlertTriangle className="h-6 w-6" />
      </div>
      <h1 className="text-2xl font-semibold text-foreground">This page couldn't render</h1>
      <p className="max-w-md break-words font-mono text-xs text-muted-foreground">{message}</p>
      <div className="flex items-center gap-2">
        <Button onClick={() => window.location.reload()}>Reload page</Button>
        <Button asChild variant="secondary">
          <Link to="/">Back to panel</Link>
        </Button>
      </div>
    </div>
  )
}

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
