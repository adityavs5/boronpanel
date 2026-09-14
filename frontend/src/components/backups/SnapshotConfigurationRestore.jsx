import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RotateCcw } from 'lucide-react'
import { get, post } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { toast } from '@/components/ui/Toast'

export function SnapshotConfigurationRestore({ username, run }) {
  const [open, setOpen] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots/runs/${run.id}`
  const catalog = useQuery({ queryKey: ['snapshot-configuration', username, run.id],
    queryFn: () => get(`${base}/configuration`), enabled: open && !!username })
  const restore = useMutation({
    mutationFn: () => post(`${base}/restore`, { kind: 'config', config_sections: ['cron'], confirmation }),
    onSuccess: () => {
      toast.success('Scheduled-task restore queued', 'Progress appears in restore history.')
      setOpen(false); setConfirmation('')
      qc.invalidateQueries({ queryKey: ['snapshot-restores', username] })
    },
  })
  if (!username || !run.snapshot_id || run.status === 'expired' || !run.options?.components?.includes('config')) return null
  return <section className="space-y-3 border-t border-border pt-4">
    <Button variant="secondary" onClick={() => setOpen(value => !value)}><RotateCcw className="h-4 w-4" />Restore scheduled tasks</Button>
    {open && <form aria-label="Scheduled-task restore" className="space-y-4 rounded-btn border border-border bg-muted/20 p-4" onSubmit={event => { event.preventDefault(); restore.mutate() }}>
      <p className="text-sm text-muted-foreground">Replace this account’s complete crontab, including manually added tasks, environment settings and cron email settings. The current configuration is saved in an encrypted recovery copy first.</p>
      {catalog.isLoading && <p className="text-sm">Checking recovery point…</p>}
      {catalog.error && <p role="alert" className="text-sm text-danger">{catalog.error.message}</p>}
      {catalog.data && !catalog.data.cron_available && <p className="text-sm">{catalog.data.reason}</p>}
      {catalog.data?.cron_available && <>
        <FormField label="Confirm account username" hint={`Type ${username} to replace its scheduled tasks.`}>
          <Input value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" />
        </FormField>
        {restore.error && <p role="alert" className="text-sm text-danger">{restore.error.message}</p>}
        <Button type="submit" loading={restore.isPending} disabled={confirmation !== username}>Restore scheduled tasks now</Button>
      </>}
    </form>}
  </section>
}
