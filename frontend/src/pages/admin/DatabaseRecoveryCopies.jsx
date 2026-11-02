import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, DatabaseBackup, RotateCcw, Trash2 } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { formatBytes, formatDate } from '@/lib/utils'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { toast } from '@/components/ui/Toast'

// Copies a failed SQL import left behind (database-import-safety). While an
// account holds `limit` of them, its imports are refused until an
// administrator restores or releases them here. Restoring keeps what the
// database holds now as one more copy (origin before_restore), and so does a
// restore that fails, so the list is read again after every attempt.
export function DatabaseRecoveryCopies({ username }) {
  const qc = useQueryClient()
  const key = ['database-recovery-copies', username]
  const base = `/api/v1/accounts/${encodeURIComponent(username)}/databases/recovery-copies`
  const [confirm, setConfirm] = useState(null) // {action: 'restore' | 'release', copy}
  const { data, isLoading, error, refetch } = useQuery({ queryKey: key, queryFn: () => get(base), enabled: !!username })
  const refresh = () => qc.invalidateQueries({ queryKey: key })
  const restore = useMutation({
    mutationFn: copy => post(`${base}/${encodeURIComponent(copy.id)}/restore`, { confirm: true }),
    onSuccess: result => {
      toast.success(`${result.db_name} restored`, result.warning)
      setConfirm(null)
      qc.invalidateQueries({ queryKey: ['databases', username] })
    },
    onSettled: refresh,
  })
  const release = useMutation({
    mutationFn: copy => del(`${base}/${encodeURIComponent(copy.id)}`, { params: { confirm: true } }),
    onSuccess: () => { toast.success('Recovery copy released'); setConfirm(null) },
    onSettled: refresh,
  })
  const active = confirm?.action === 'restore' ? restore : release
  const open = next => { restore.reset(); release.reset(); setConfirm(next) }
  const copies = data?.copies || []
  const taken = copy => formatDate(copy.created_at)

  return (
    <Card className="mt-6">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2"><DatabaseBackup className="h-4 w-4" /> Recovery copies</CardTitle>
          <CardDescription>Copies of a database taken before an SQL import that failed and could not be rolled back automatically. Restore one to put the database back, or release it once it is not needed.</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? <CardSkeleton /> : error ? <ErrorState error={error} onRetry={refetch} /> : <>
          {data?.imports_blocked && (
            <p role="status" className="flex items-start gap-2 rounded-btn border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-foreground">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden="true" />
              SQL imports for this account are blocked until recovery copies are restored or released.
            </p>
          )}
          {copies.length === 0 ? <p className="text-sm text-muted-foreground">This account has no recovery copies.</p> : (
            <ul className="divide-y divide-border rounded-btn border border-border" aria-label="Recovery copies">
              {copies.map(copy => (
                <li key={copy.id} className="space-y-2 px-4 py-3 text-sm">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="break-all font-medium text-foreground">{copy.db_name || 'Unknown database'}</p>
                      <p className="text-muted-foreground">Taken {taken(copy)} · {formatBytes(copy.size_bytes || 0)}{!copy.complete && ' · incomplete'}</p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" variant="secondary" disabled={!copy.restorable || restore.isPending || release.isPending} onClick={() => open({ action: 'restore', copy })}><RotateCcw className="h-4 w-4" /> Restore</Button>
                      <Button size="sm" variant="danger-ghost" disabled={restore.isPending || release.isPending} onClick={() => open({ action: 'release', copy })}><Trash2 className="h-4 w-4" /> Release</Button>
                    </div>
                  </div>
                  {copy.description && <p className="break-words text-foreground">{copy.description}</p>}
                  {copy.import_error && <p className="break-words text-muted-foreground">MariaDB reported: {copy.import_error}</p>}
                  {copy.rollback_error && (
                    <details className="text-muted-foreground">
                      <summary className="cursor-pointer rounded-sm focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/30">Why the automatic rollback failed</summary>
                      <p className="mt-1 whitespace-pre-wrap break-words font-mono text-xs">{copy.rollback_error}</p>
                    </details>
                  )}
                  {!copy.restorable && copy.reason && <p className="flex items-start gap-1.5 break-words text-foreground"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />{copy.reason}</p>}
                </li>
              ))}
            </ul>
          )}
        </>}
      </CardContent>
      <ConfirmDialog
        open={!!confirm}
        onOpenChange={value => { if (!value && !active.isPending) setConfirm(null) }}
        title={confirm?.action === 'restore' ? `Restore ${confirm.copy.db_name}?` : 'Release this recovery copy?'}
        description={confirm?.action === 'restore'
          ? `This replaces everything ${confirm.copy.db_name} holds now with the copy taken ${taken(confirm.copy)}. What it holds now is kept as a new recovery copy first.`
          : confirm ? `Permanently delete this copy of ${confirm.copy.db_name || 'the database'} taken ${taken(confirm.copy)}? This cannot be undone.` : undefined}
        confirmLabel={confirm?.action === 'restore' ? 'Restore copy' : 'Delete copy'}
        variant={confirm?.action === 'restore' ? 'primary' : 'danger'}
        loading={active.isPending}
        loadingDescription={confirm?.action === 'restore' ? 'Restoring… this can take several minutes' : undefined}
        error={active.error}
        onConfirm={() => confirm && active.mutate(confirm.copy)}
      />
    </Card>
  )
}
