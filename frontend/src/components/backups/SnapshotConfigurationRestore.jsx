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
  const dns = section === 'dns'
  const labels = {php: 'PHP settings', cron: 'scheduled tasks', dns: 'DNS records'}
  const label = labels[section] || labels.cron
  const [zones, setZones] = useState([])
  const available = data => !!data?.[`${section}_available`]
  const [confirmation, setConfirmation] = useState('')
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots/runs/${run.id}`
  const catalog = useQuery({ queryKey: ['snapshot-configuration', username, run.id],
    queryFn: () => get(`${base}/configuration`), enabled: open && !!username })
  const validZones = zones.length > 0 && zones.every(name => catalog.data?.dns_zones?.some(zone => zone.zone === name && zone.available))
  const restore = useMutation({
    mutationFn: () => post(`${base}/restore`, { kind: 'config', config_sections: [section], confirmation, ...(dns ? {dns_zones: zones} : {}) }),
    onSuccess: () => {
      toast.success(`${label} restore queued`, 'Progress appears in restore history.')
      setSection(null); setConfirmation(''); setZones([])
      qc.invalidateQueries({ queryKey: ['snapshot-restores', username] })
    },
  })
  if (!username || !run.snapshot_id || run.status === 'expired' || !run.options?.components?.includes('config')) return null
  return <section className="space-y-3 border-t border-border pt-4">
    <div className="flex flex-wrap gap-2">{['cron', 'php', 'dns'].map(value => <Button key={value} variant="secondary" disabled={restore.isPending} onClick={() => { setSection(section === value ? null : value); setConfirmation(''); setZones([]); restore.reset() }}><RotateCcw className="h-4 w-4" />Restore {labels[value]}</Button>)}</div>
    {open && <form aria-label={dns ? 'DNS records restore' : php ? 'PHP settings restore' : 'Scheduled-task restore'} className="space-y-4 rounded-btn border border-border bg-muted/20 p-4" onSubmit={event => { event.preventDefault(); if (confirmation === username && available(catalog.data) && !catalog.isFetching && !catalog.error && !restore.isPending && (!dns || validZones)) restore.mutate() }}>
      <p className="text-sm text-muted-foreground">{dns ? 'Replace customer DNS records in the selected zones with this backup, including removal of records added afterward. This can affect websites and email. Current records are saved in an encrypted recovery copy first. Server-managed nameservers and DNSSEC settings are kept. DNS changes may take time to propagate.' : php ? 'Restore the account’s default PHP version, saved site overrides, limits and extension choices. These settings affect your websites. Administrator function restrictions are kept. The current PHP settings are saved in an encrypted recovery copy first.' : 'Replace this account’s complete crontab, including manually added tasks, environment settings and cron email settings. The current configuration is saved in an encrypted recovery copy first.'}</p>
      {catalog.isLoading && <p className="text-sm">Checking recovery point…</p>}
      {catalog.error && <p role="alert" className="text-sm text-danger">{catalog.error.message}</p>}
      {catalog.data && !available(catalog.data) && <p className="text-sm">{catalog.data[`${section}_reason`] || catalog.data.reason || 'This recovery point does not contain these settings.'}</p>}
      {dns && !!catalog.data?.dns_zones?.length && <fieldset disabled={restore.isPending} className="space-y-2">
        <legend className="mb-2 text-sm font-medium">Choose DNS zones</legend>
        {catalog.data.dns_zones.map(zone => <label key={zone.zone} className="flex items-start gap-3 rounded-btn border border-border bg-background p-3">
          <input type="checkbox" className="mt-1 h-4 w-4 accent-accent" disabled={!zone.available} checked={zones.includes(zone.zone)} onChange={event => { setZones(current => event.target.checked ? [...current, zone.zone] : current.filter(name => name !== zone.zone)); setConfirmation('') }} />
          <span className="min-w-0"><span className="block break-all text-sm font-medium">{zone.zone}</span><span className="text-xs text-muted-foreground">{zone.available ? `${zone.provider === 'cloudflare' ? 'Cloudflare · ' : ''}${zone.record_count} ${zone.provider === 'cloudflare' ? 'records' : 'record sets'}` : zone.reason || 'Unavailable for recovery'}</span></span>
        </label>)}
      </fieldset>}
      {available(catalog.data) && <>
        {php && <p className="text-sm">Saved default: PHP {catalog.data.php_default_version} · {catalog.data.php_sites} sites</p>}
        <FormField label="Confirm account username" hint={`Type ${username} to replace its ${label}.`}>
          <Input value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" />
        </FormField>
        {restore.error && <p role="alert" className="text-sm text-danger">{restore.error.message}</p>}
        <Button type="submit" loading={restore.isPending} disabled={confirmation !== username || catalog.isFetching || !!catalog.error || (dns && !validZones)}>Restore {label} now</Button>
      </>}
    </form>}
  </section>
}
