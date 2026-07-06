import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { StickyNote, Plus } from 'lucide-react'
import { get, post } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Input'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { EmptyState, ErrorState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'

// Phase 8 feature 11: admin-only, append-only account notes. This tab is only
// ever rendered on the admin account-detail page — never on any customer view.
export default function AccountNotes({ username }) {
  const qc = useQueryClient()
  const [body, setBody] = useState('')
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['account-notes', username],
    queryFn: () => get(`/api/v1/admin/accounts/${username}/notes`),
    enabled: !!username,
  })

  const addMut = useMutation({
    mutationFn: () => post(`/api/v1/admin/accounts/${username}/notes`, { body: body.trim() }),
    onSuccess: () => { toast.success('Note added'); setBody(''); qc.invalidateQueries({ queryKey: ['account-notes', username] }) },
    onError: (e) => toast.error('Could not add note', e.message),
  })

  const notes = data?.notes || []

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><StickyNote className="h-4 w-4" /> Add a note</CardTitle>
          <CardDescription>Internal, admin-only, append-only. Never visible to the customer.</CardDescription>
        </CardHeader>
        <CardContent>
          <Textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} placeholder="e.g. Customer requested a quota increase — approved." />
        </CardContent>
        <CardFooter>
          <Button loading={addMut.isPending} disabled={!body.trim()} onClick={() => addMut.mutate()}>
            <Plus className="h-4 w-4" /> Add note
          </Button>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader><CardTitle>Notes ({notes.length})</CardTitle></CardHeader>
        <CardContent>
          {isLoading ? (
            <CenteredSpinner />
          ) : error ? (
            <ErrorState error={error} onRetry={refetch} />
          ) : notes.length === 0 ? (
            <EmptyState icon={StickyNote} title="No notes yet" description="Notes you add here are only visible to admins." />
          ) : (
            <ul className="space-y-3">
              {notes.map((n) => (
                <li key={n.id} className="rounded-card border border-border px-4 py-3">
                  <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">{n.author}</span>
                    <span>{formatDate(n.created_at)}</span>
                  </div>
                  <p className="whitespace-pre-wrap text-sm text-foreground">{n.body}</p>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
