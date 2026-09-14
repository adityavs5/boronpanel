import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RotateCcw } from 'lucide-react'
import { get, post } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { toast } from '@/components/ui/Toast'

export function SnapshotConfigurationRestore({ username, run }) {
  const [section, setSection] = useState(null)
  const open = section !== null
  const php = section === 'php'
  const label = php ? 'PHP settings' : 'scheduled tasks'
  const available = data => !!data?.[`${section}_available`]
  const [confirmation, setConfirmation] = useState('')
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots/runs/${run.id}`
  const catalog = useQuery({ queryKey: ['snapshot-configuration', username, run.id],
    queryFn: () => get(`${base}/configuration`), enabled: open && !!username })
  const restore = useMutation({
    mutationFn: () => post(`${base}/restore`, { kind: 'config', config_sections: [section], confirmation }),
    onSuccess: () => {
      toast.success(`${php ? 'PHP settings' : 'Scheduled-task'} restore queued`, 'Progress appears in restore history.')
      setSection(null); setConfirmation('')
      qc.invalidateQueries({ queryKey: ['snapshot-restores', username] })
    },
  })
  if (!username || !run.snapshot_id || run.status === 'expired' || !run.options?.components?.includes('config')) return null
  return <section className="space-y-3 border-t border-border pt-4">
    <div className="flex flex-wrap gap-2">{['cron', 'php'].map(value => <Button key={value} variant="secondary" disabled={restore.isPending} onClick={() => { setSection(section === value ? null : value); setConfirmation(''); restore.reset() }}><RotateCcw className="h-4 w-4" />Restore {value === 'php' ? 'PHP settings' : 'scheduled tasks'}</Button>)}</div>
    {open && <form aria-label={php ? 'PHP settings restore' : 'Scheduled-task restore'} className="space-y-4 rounded-btn border border-border bg-muted/20 p-4" onSubmit={event => { event.preventDefault(); if (confirmation === username && available(catalog.data) && !catalog.isFetching && !restore.isPending) restore.mutate() }}>
      <p className="text-sm text-muted-foreground">{php ? 'Restore the account’s default PHP version, saved site overrides, limits and extension choices. These settings affect your websites. Administrator function restrictions are kept. The current PHP settings are saved in an encrypted recovery copy first.' : 'Replace this account’s complete crontab, including manually added tasks, environment settings and cron email settings. The current configuration is saved in an encrypted recovery copy first.'}</p>
      {catalog.isLoading && <p className="text-sm">Checking recovery point…</p>}
      {catalog.error && <p role="alert" className="text-sm text-danger">{catalog.error.message}</p>}
      {catalog.data && !available(catalog.data) && <p className="text-sm">{catalog.data[`${section}_reason`] || catalog.data.reason || 'This recovery point does not contain these settings.'}</p>}
      {available(catalog.data) && <>
        {php && <p className="text-sm">Saved default: PHP {catalog.data.php_default_version} · {catalog.data.php_sites} sites</p>}
        <FormField label="Confirm account username" hint={`Type ${username} to replace its ${label}.`}>
          <Input value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" />
        </FormField>
        {restore.error && <p role="alert" className="text-sm text-danger">{restore.error.message}</p>}
        <Button type="submit" loading={restore.isPending} disabled={confirmation !== username || catalog.isFetching || !!catalog.error}>Restore {label} now</Button>
      </>}
    </form>}
  </section>
}
