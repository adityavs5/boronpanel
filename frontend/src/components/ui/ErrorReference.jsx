import { Copy } from 'lucide-react'
import { BASE_PATH } from '@/lib/api'
import { cn } from '@/lib/cn'
import { useAuth } from '@/store/auth'

const REFERENCE = /reference:\s*([a-f0-9]{16})/i

// The 16-hex-digit error reference carried in a message ("… (reference: x)").
export function referenceIn(text) {
  return String(text || '').match(REFERENCE)?.[1]?.toLowerCase() || null
}

// An error reference with a copy action, and for administrators a link to the
// matching Error Log entry. The Toaster renders outside the router, so the
// link is a plain anchor.
export function ErrorReference({ reference, className }) {
  const admin = useAuth((state) => state.role === 'admin')
  if (!reference) return null
  return (
    <div className={cn('mt-1 flex flex-wrap items-center gap-2 text-sm', className)}>
      <code className="text-muted-foreground">{reference}</code>
      <button
        type="button"
        className="inline-flex items-center gap-1 rounded-sm text-link underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => navigator.clipboard?.writeText(reference)}
        aria-label="Copy error reference"
      >
        <Copy className="h-3.5 w-3.5" /> Copy
      </button>
      {admin && <a className="rounded-sm text-link underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" href={`${BASE_PATH}/error-log?reference=${reference}`}>View error details</a>}
    </div>
  )
}
