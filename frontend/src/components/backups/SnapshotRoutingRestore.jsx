import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Mail } from 'lucide-react'
import { get, post } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { FormField, Input } from '@/components/ui/Input'
import { toast } from '@/components/ui/Toast'

export function SnapshotRoutingRestore({ username, run }) {
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState([])
  const [search, setSearch] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots/runs/${run.id}`
  const catalog = useQuery({ queryKey: ['snapshot-mail-routing', username, run.id], queryFn: () => get(`${base}/mail-routing`), enabled: open && !!username })
  const rows = catalog.data?.domains || []
  const visible = rows.filter(row => row.domain.toLowerCase().includes(search.trim().toLowerCase()))
  const valid = selected.length > 0 && selected.every(domain => rows.some(row => row.domain === domain && row.available))
  const ready = valid && acknowledged && confirmation === username && !catalog.isFetching && !catalog.error
  const restore = useMutation({
    mutationFn: () => post(`${base}/restore`, { kind: 'mail_routing', mail_domains: selected, confirmation, mail_pause_acknowledged: acknowledged }),
    onSuccess: () => {
      toast.success('Email settings restore queued', 'Follow its progress in restore history.')
      setOpen(false); setSelected([]); setConfirmation(''); setAcknowledged(false)
      qc.invalidateQueries({ queryKey: ['snapshot-restores', username] })
    },
  })
  if (!username || !run.snapshot_id || run.status === 'expired' || !run.options?.components?.includes('mail')) return null
  return <section className="space-y-3 border-t border-border pt-4">
    <Button variant="secondary" onClick={() => setOpen(value => !value)}> <Mail className="h-4 w-4"/>Restore email settings</Button>
    {open && <form aria-label="Email settings restore" className="space-y-4 rounded-btn border border-border bg-muted/20 p-4" onSubmit={event => { event.preventDefault(); if (ready && !restore.isPending) restore.mutate() }}>
      <div><h3 className="font-semibold">Choose domains to restore</h3><p className="mt-1 text-sm text-muted-foreground">Restore forwarders, catch-all addresses and automatic replies for selected domains. Current settings are saved in an encrypted recovery copy first. Mailbox messages, passwords and quotas are kept.</p></div>
      <p className="text-sm text-muted-foreground">Mail access across this server pauses briefly while settings are switched. Email clients may reconnect and incoming mail is queued.</p>
      {catalog.isLoading ? <p role="status">Loading backed-up email settings…</p> : catalog.error ? <div role="alert"><p className="text-sm text-danger">{catalog.error.message}</p><Button type="button" variant="secondary" onClick={() => catalog.refetch()}>Try again</Button></div> : rows.length ? <>
        <Input aria-label="Search backed-up email domains" placeholder="Search domains…" value={search} onChange={event => setSearch(event.target.value)}/>
        <div className="flex flex-wrap items-center gap-3 text-sm"><span>{selected.length} selected</span><Button type="button" size="sm" variant="ghost" disabled={restore.isPending} onClick={() => setSelected(visible.filter(row => row.available).map(row => row.domain))}>Select shown</Button><Button type="button" size="sm" variant="ghost" disabled={restore.isPending} onClick={() => setSelected([])}>Clear selection</Button></div>
        <fieldset disabled={restore.isPending} className="max-h-72 space-y-2 overflow-y-auto"><legend className="sr-only">Email domains in this recovery point</legend>{visible.map(row => <label key={row.domain} className="flex items-start gap-3 rounded-btn border border-border bg-background p-3"><input type="checkbox" className="mt-1 h-4 w-4 accent-accent" checked={selected.includes(row.domain)} disabled={!row.available} onChange={event => setSelected(values => event.target.checked ? [...values, row.domain] : values.filter(value => value !== row.domain))}/><span className="min-w-0"><span className="block break-all text-sm font-medium">{row.domain}</span><span className="text-xs text-muted-foreground">{row.reason || `${row.forwarders || 0} forwarders · ${row.catchall ? 'Catch-all configured' : 'No catch-all'} · ${row.autoresponders || 0} automatic replies`}</span></span></label>)}</fieldset>
        {!visible.length && <p className="text-sm text-muted-foreground">No matching domains.</p>}
      </> : <p className="text-sm text-muted-foreground">{catalog.data?.reason || 'This recovery point has no email settings recovery metadata.'}</p>}
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 h-4 w-4 accent-accent" disabled={restore.isPending} checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)}/><span>I understand that mail access will be interrupted briefly.</span></label>
      <FormField label={`Type ${username} to confirm email settings restore`} htmlFor={`routing-restore-confirm-${run.id}`}><Input id={`routing-restore-confirm-${run.id}`} disabled={restore.isPending} value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" spellCheck={false}/></FormField>
      {restore.error && <p role="alert" className="text-sm text-danger">{restore.error.message}</p>}
      <div className="flex flex-wrap gap-2"><Button type="submit" loading={restore.isPending} disabled={!ready}>Restore selected email settings</Button><Button type="button" variant="ghost" disabled={restore.isPending} onClick={() => setOpen(false)}>Cancel</Button></div>
    </form>}
  </section>
}
