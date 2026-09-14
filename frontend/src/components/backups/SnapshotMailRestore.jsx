import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Mail } from 'lucide-react'
import { get, post } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { FormField, Input } from '@/components/ui/Input'
import { toast } from '@/components/ui/Toast'

export function SnapshotMailRestore({ username, run }) {
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState([])
  const [search, setSearch] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const qc = useQueryClient()
  const base = `/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots/runs/${run.id}`
  const catalog = useQuery({ queryKey: ['snapshot-mailboxes', username, run.id], queryFn: () => get(`${base}/mailboxes`), enabled: open && !!username })
  const rows = catalog.data?.mailboxes || []
  const visible = rows.filter(row => row.address.toLowerCase().includes(search.trim().toLowerCase()))
  const valid = selected.length > 0 && selected.every(address => rows.some(row => row.address === address && row.available))
  const restore = useMutation({
    mutationFn: () => post(`${base}/restore`, { kind: 'mail', mailboxes: selected, confirmation, mail_pause_acknowledged: acknowledged }),
    onSuccess: () => {
      toast.success('Mailbox restore queued', 'Follow its progress in restore history.')
      setOpen(false); setSelected([]); setConfirmation(''); setAcknowledged(false)
      qc.invalidateQueries({ queryKey: ['snapshot-restores', username] })
    },
  })
  if (!username || !run.snapshot_id || run.status === 'expired' || !run.options?.components?.includes('mail')) return null
  return <section className="space-y-3 border-t border-border pt-4">
    <Button variant="secondary" onClick={() => setOpen(value => !value)}><Mail className="h-4 w-4"/>Restore mailboxes</Button>
    {open && <form aria-label="Mailbox restore" className="space-y-4 rounded-btn border border-border bg-muted/20 p-4" onSubmit={event => { event.preventDefault(); if (valid && acknowledged && confirmation === username) restore.mutate() }}>
      <div><h3 className="font-semibold">Choose mailboxes to restore</h3><p className="mt-1 text-sm text-muted-foreground">Selected mailbox contents will be replaced by this recovery point. Current contents are preserved in an encrypted safety copy. Existing passwords and settings are retained; deleted mailboxes use their saved credentials.</p></div>
      <p className="text-sm text-muted-foreground">Selected mailboxes may be unavailable while the restore runs. Switching the prepared copies briefly pauses mail access across this server; email clients may reconnect and incoming mail is queued.</p>
      {catalog.isLoading ? <p role="status">Loading backed-up mailboxes…</p> : catalog.error ? <div role="alert"><p className="text-sm text-danger">{catalog.error.message}</p><Button type="button" variant="secondary" onClick={() => catalog.refetch()}>Try again</Button></div> : rows.length ? <>
        <Input aria-label="Search backed-up mailboxes" placeholder="Search email addresses…" value={search} onChange={event => setSearch(event.target.value)}/>
        <div className="flex flex-wrap items-center gap-3 text-sm"><span>{selected.length} selected</span><Button type="button" size="sm" variant="ghost" disabled={restore.isPending} onClick={() => setSelected(visible.filter(row => row.available).slice(0, 1000).map(row => row.address))}>Select shown</Button><Button type="button" size="sm" variant="ghost" disabled={restore.isPending} onClick={() => setSelected([])}>Clear selection</Button></div>
        <fieldset disabled={restore.isPending} className="max-h-72 space-y-2 overflow-y-auto"><legend className="sr-only">Mailboxes in this recovery point</legend>{visible.map(row => <label key={row.address} className="flex items-start gap-3 rounded-btn border border-border bg-background p-3"><input type="checkbox" className="mt-1 h-4 w-4 accent-accent" checked={selected.includes(row.address)} disabled={!row.available || (!selected.includes(row.address) && selected.length >= 1000)} onChange={event => setSelected(values => event.target.checked ? [...values, row.address] : values.filter(value => value !== row.address))}/><span className="min-w-0"><span className="block break-all text-sm font-medium">{row.address}</span><span className="text-xs text-muted-foreground">{row.reason || (row.action === 'recreate' ? 'Deleted mailbox · restore with saved credentials' : 'Existing mailbox · replace messages')}</span></span></label>)}</fieldset>
        {!visible.length && <p className="text-sm text-muted-foreground">No matching mailboxes.</p>}
      </> : <p className="text-sm text-muted-foreground">This recovery point has no mailbox recovery metadata.</p>}
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 h-4 w-4 accent-accent" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)}/><span>I understand that mail access will be interrupted briefly.</span></label>
      <FormField label={`Type ${username} to confirm mailbox restore`} htmlFor={`mail-restore-confirm-${run.id}`}><Input id={`mail-restore-confirm-${run.id}`} value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" spellCheck={false}/></FormField>
      {restore.error && <p role="alert" className="text-sm text-danger">{restore.error.message}</p>}
      <div className="flex flex-wrap gap-2"><Button type="submit" loading={restore.isPending} disabled={!valid || !acknowledged || confirmation !== username || catalog.isFetching || !!catalog.error}>Restore selected mailboxes</Button><Button type="button" variant="ghost" disabled={restore.isPending} onClick={() => setOpen(false)}>Cancel</Button></div>
    </form>}
  </section>
}
